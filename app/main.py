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

import logging
import concurrent.futures

from fastapi import FastAPI, Request, Response

from app.adapters.claude_extractor import ClaudeExtractor
from app.adapters.csv_inventory import CsvInventoryRepository
from app.adapters.fuzzy_matcher import FuzzyBookMatcher
from app.adapters.sqlite_store import SqliteInquiryStore
from app.adapters.whatsapp_client import WhatsAppClient, parse_webhook, to_incoming
from app.config import load_settings
from app.review.routes import build_router
from app.services.inquiry_service import InquiryService
from app.services.quote_service import WhatsAppQuoteRenderer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bookstore")

settings = load_settings()

# ---- wire the graph (Dependency Injection, done by hand, no framework needed) ----
inventory = CsvInventoryRepository(settings.inventory_csv)
matcher = FuzzyBookMatcher(
    inventory,
    candidate_floor=settings.candidate_floor,
    confident_threshold=settings.auto_send_threshold,
)
extractor = ClaudeExtractor(settings.anthropic_api_key, settings.extraction_model)
store = SqliteInquiryStore(settings.db_path)
whatsapp = WhatsAppClient(settings.wa_access_token, settings.wa_phone_number_id)
renderer = WhatsAppQuoteRenderer(store_name="our bookstore")
service = InquiryService(
    extractor=extractor,
    matcher=matcher,
    store=store,
    messenger=whatsapp,
    renderer=renderer,
    auto_send_threshold=settings.auto_send_threshold,
)

# ---- background worker ----
# Meta requires a fast 200 on the webhook or it retries; extraction takes
# seconds. We use a ThreadPoolExecutor to process multiple messages concurrently.
# Trade-off: jobs in memory are lost on restart. For extreme scale, use Redis.
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

def _process_job(raw: dict) -> None:
    try:
        msg = to_incoming(raw, whatsapp)
        if msg is None:
            return  # statuses, reactions, audio... ignore quietly
        service.handle_message(msg)
    except Exception:
        log.exception("Failed processing message %s", raw.get("id"))

# ---- app ----
app = FastAPI(title="Bookstore Quote Bot", docs_url=None, redoc_url=None)
app.include_router(build_router(service, store, settings))

@app.get("/health")
def health() -> dict:
    return {"ok": True, "inventory_items": len(inventory.all_items()), **store.stats()}

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
    payload = await request.json()
    for raw in parse_webhook(payload):
        if store.mark_seen(raw.get("id", "")):  # dedup Meta's redeliveries
            _executor.submit(_process_job, raw)
    return {"status": "queued"}  # always 200, always fast