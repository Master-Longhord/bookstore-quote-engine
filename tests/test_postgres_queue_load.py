from __future__ import annotations

import os
import sys
import time
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock

# Block heavy third-party renderers
mock_weasyprint = MagicMock()
sys.modules["weasyprint"] = mock_weasyprint

from app.adapters.postgres_store import PostgresInquiryStore
from app.domain.models import IncomingMessage, MediaKind
from app.services.inquiry_service import InquiryService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pg_stress_test")

TOTAL_JOBS = 1000
WORKER_THREADS = 15 

# Provide a local test DB URL. Do NOT run this against production.
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")

def run_real_postgres_stress_test():
    # 1. Initialize the REAL Postgres store
    store = PostgresInquiryStore(TEST_DB_URL)
    
    # Mock external APIs so we strictly test DB and local worker throughput
    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [MagicMock(title="Grade 7 Science", quantity=1)]
    mock_matcher = MagicMock()
    mock_matcher.match.return_value = MagicMock(best=None, alternatives=[])
    
    service = InquiryService(
        extractor=mock_extractor,
        matcher=mock_matcher,
        store=store,
        messenger=MagicMock(),
        renderer=MagicMock(),
        auto_send_threshold=80.0,
    )

    # 2. Phase 1: Bulk Enqueue Benchmark (Simulating Webhook Spike)
    log.info("Phase 1: Ingesting %d webhooks into PostgreSQL...", TOTAL_JOBS)
    
    payloads = []
    for idx in range(TOTAL_JOBS):
        msg_id = f"wamid.{uuid.uuid4().hex[:15]}"
        raw_payload = {
            "messaging_product": "whatsapp",
            "from": f"1876555{idx:04d}",
            "text": {"body": "First Steps in Reading Book 1 for Grade 2"}
        }
        payloads.append((msg_id, raw_payload))

    enqueue_start = time.perf_counter()
    
    # Execute sequentially to mimic how the webhook router would call it one by one
    for msg_id, raw in payloads:
        store.mark_seen_and_enqueue(channel_message_id=msg_id, raw_payload=raw)

    enqueue_time = time.perf_counter() - enqueue_start
    log.info("Enqueued %d jobs in %.2f seconds (%.2f ops/sec)", 
             TOTAL_JOBS, enqueue_time, TOTAL_JOBS / enqueue_time)

    # 3. Phase 2: Concurrent Worker Queue Drain (SKIP LOCKED)
    log.info("Phase 2: Draining queue using %d concurrent workers...", WORKER_THREADS)
    drain_start = time.perf_counter()
    
    processed_count = 0
    errors: list[str] = []

    def worker_loop():
        local_processed = 0
        while True:
            try:
                # Use the exact method from your PostgresInquiryStore
                job = store.claim_next_job()
                if not job:
                    break  # Queue is empty
                
                # Simulate passing the payload to the service layer
                # In real life: msg = to_incoming(job["payload"])
                # service.handle_message(msg)
                
                time.sleep(0.005)  # Simulate fast internal processing
                store.complete_job(job["id"])
                
                local_processed += 1
            except Exception as e:
                errors.append(str(e))
                if 'job' in locals() and job:
                    store.fail_job(job["id"], str(e))
                break
                
        return local_processed

    with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
        futures = [executor.submit(worker_loop) for _ in range(WORKER_THREADS)]
        for future in as_completed(futures):
            processed_count += future.result()

    drain_time = time.perf_counter() - drain_start
    drain_throughput = processed_count / drain_time

    # 4. Results Report
    print("\n================ POSTGRES QUEUE BENCHMARK ================")
    print(f"Total Enqueued Items    : {TOTAL_JOBS}")
    print(f"Total Worker Threads    : {WORKER_THREADS}")
    print(f"Queue Drain Time        : {drain_time:.2f} seconds")
    print(f"Effective Throughput    : {drain_throughput:.2f} jobs/sec")
    print(f"Errors Encountered      : {len(errors)}")
    
    stats = store.job_stats()
    print(f"Final DB Job Stats      : {stats}")
    print("==========================================================")

if __name__ == "__main__":
    run_real_postgres_stress_test()