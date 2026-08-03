"""FastAPI entrypoint and composition root.

This is the ONLY file that knows which concrete adapters are in play.
Swap any of them here (e.g. CSV -> Postgres inventory) and nothing else
in the codebase changes.

Endpoints:
  GET  /webhook          Meta's one-time verification handshake
  POST /webhook          inbound WhatsApp messages
  GET  /review           human review dashboard (token-protected)
  POST /review/{id}/approve
  GET  /health           for Railway/UptimeRobot
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response

from app.adapters.claude_extractor import ClaudeExtractor
from app.adapters.csv_inventory import CsvInventoryRepository
from app.adapters.fuzzy_matcher import FuzzyBookMatcher
from app.adapters.postgres_store import PostgresInquiryStore
from app.adapters.whatsapp_client import WhatsAppClient, parse_webhook, to_incoming
from app.config import load_settings
from app.review.routes import build_router
from app.services.inquiry_service import InquiryService
from app.services.quote_service import WhatsAppQuoteRenderer
from app.services.faq_service import process_faq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("BookDepot")

settings = load_settings()

# ---- wire the graph (Dependency Injection, done by hand, no framework needed) ----
inventory = CsvInventoryRepository(settings.inventory_csv)
matcher = FuzzyBookMatcher(
    inventory,
    candidate_floor=settings.candidate_floor,
    confident_threshold=settings.auto_send_threshold,
)
extractor = ClaudeExtractor(settings.anthropic_api_key, settings.extraction_model)
store = PostgresInquiryStore(settings.database_url)
whatsapp = WhatsAppClient(settings.wa_access_token, settings.wa_phone_number_id)
renderer = WhatsAppQuoteRenderer(store_name="BookDepot")
service = InquiryService(
    extractor=extractor,
    matcher=matcher,
    store=store,
    inventory=inventory,
    messenger=whatsapp,
    renderer=renderer,
    auto_send_threshold=settings.auto_send_threshold,
)

# ---- background worker ----
# Jobs are persisted to Postgres (see JobModel / claim_next_job in
# postgres_store.py) instead of an in-memory ThreadPoolExecutor. If the
# app crashes or restarts mid-backlog, pending/processing jobs survive on
# disk and get picked back up -- nothing is silently lost.
WORKER_COUNT = 5
POLL_INTERVAL_SECONDS = 2
SWEEP_INTERVAL_SECONDS = 60
STUCK_JOB_TIMEOUT_SECONDS = 300

# Global event to signal threads to stop cleanly
shutdown_event = threading.Event()


def _process_job(raw: dict, is_last_attempt: bool = False) -> None:
    log.info("Processing raw payload: %s", raw)
    msg = to_incoming(raw, whatsapp)
    if msg is None:
        log.info("Skipped: Payload is not an incoming user message (likely delivery status).")
        return

    log.info("Parsed message from %s: '%s'", msg.sender, msg.text)

    # Intercept FAQs before running the heavy Claude extraction pipeline
    if msg.text and process_faq(msg.text, msg.sender, whatsapp):
        log.info("Message handled by FAQ service. Skipping extraction.")
        return

    service.handle_message(msg, is_last_attempt=is_last_attempt)
    log.info("Message handled successfully.")


def _worker_loop() -> None:
    while not shutdown_event.is_set():
        job = store.claim_next_job()
        if job is None:
            shutdown_event.wait(POLL_INTERVAL_SECONDS)
            continue

        # Calculate if this execution will exhaust the max attempts
        is_last = (job["attempts"] + 1) >= job["max_attempts"]

        try:
            _process_job(job["payload"], is_last_attempt=is_last)
            store.complete_job(job["id"])
        except Exception as exc:
            log.exception("Job %s failed on attempt %d", job["id"], job["attempts"] + 1)
            store.fail_job(job["id"], str(exc))


def _sweeper_loop() -> None:
    while not shutdown_event.is_set():
        try:
            n = store.requeue_stuck_jobs(STUCK_JOB_TIMEOUT_SECONDS)
            if n:
                log.warning("Requeued %d stuck job(s)", n)
        except Exception:
            log.exception("Sweeper loop failed")
        shutdown_event.wait(SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- STARTUP ----
    threads = []
    for i in range(WORKER_COUNT):
        t = threading.Thread(target=_worker_loop, name=f"worker-{i}")
        t.start()
        threads.append(t)

    t_sweep = threading.Thread(target=_sweeper_loop, name="sweeper")
    t_sweep.start()
    threads.append(t_sweep)

    log.info("Started %d worker thread(s) + 1 sweeper thread", WORKER_COUNT)

    yield  # Application is now running and accepting requests

    # ---- SHUTDOWN ----
    log.info("Shutting down background workers gracefully...")
    shutdown_event.set()  # Signal all loops to stop

    # Give threads up to 15 seconds to finish their current job before killing the server
    for t in threads:
        t.join(timeout=15.0)
    log.info("All background threads stopped cleanly.")


# ---- app ----
# Pass the lifespan context manager into the FastAPI app
app = FastAPI(title="Bookstore Quote Bot", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(build_router(service, store, settings))


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "inventory_items": len(inventory.all_items()),
        **store.stats(),
        "jobs": store.job_stats(),
    }


@app.get("/webhook")
def verify(request: Request) -> Response:
    """Meta's verification handshake when you save the webhook URL."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.wa_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook")
async def receive(request: Request) -> dict:
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.wa_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        log.warning("Rejected webhook: invalid signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    items = list(parse_webhook(payload))
    log.info("Received webhook containing %d item(s)", len(items))

    for raw in items:
        msg_id = raw.get("id", "")
        is_new = store.mark_seen_and_enqueue(msg_id, raw)
        log.info("Message ID '%s' -> enqueued: %s", msg_id, is_new)

    return {"status": "queued"}