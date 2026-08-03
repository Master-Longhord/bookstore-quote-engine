"""InquiryStore adapter backed by PostgreSQL and synchronous SQLAlchemy.

Maintains the exact same synchronous interface as SqliteInquiryStore,
allowing a friction-free drop-in replacement while providing Postgres stability.
"""
from __future__ import annotations

import time
import json
from typing import Optional

from sqlalchemy import Float, String, select, func, create_engine, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.adapters.sqlite_store import _deserialize, _serialize
from app.domain.models import Inquiry, InquiryStatus, PaymentStatus

# ---- SQLAlchemy Models ----

class Base(DeclarativeBase):
    pass


class InquiryModel(Base):
    __tablename__ = "inquiries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sender: Mapped[str] = mapped_column(String, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, index=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class SeenMessageModel(Base):
    __tablename__ = "seen_messages"

    channel_message_id: Mapped[str] = mapped_column(String, primary_key=True)
    seen_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)


class JobModel(Base):
    """Durable work queue backing the webhook -> processing pipeline.

    Rows are inserted by the webhook handler (atomically with mark_seen)
    and claimed by worker threads via SELECT ... FOR UPDATE SKIP LOCKED,
    so multiple workers can safely pull from the same table.
    """
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String, index=True, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)
    claimed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    retry_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)


# ---- Postgres Inquiry Store (Synchronous) ----

class PostgresInquiryStore:
    def __init__(self, database_url: str):
        # Create a synchronous connection engine tuned for heavy concurrency
        self._engine = create_engine(
            database_url, 
            pool_pre_ping=True,
            pool_size=20,       # Keep 20 connections open permanently
            max_overflow=20     # Allow up to 20 extra during sudden bursts
        )
        self._session_factory = sessionmaker(bind=self._engine)

        # Ensure tables exist on startup
        Base.metadata.create_all(self._engine)

    def _conn(self):
        return self._session_factory()

    # ---- InquiryStore port ----

    def save(self, inquiry: Inquiry) -> None:
        payload_dict = _serialize(inquiry)

        stmt = pg_insert(InquiryModel).values(
            id=inquiry.id,
            sender=inquiry.sender,
            status=inquiry.status.value,
            created_at=inquiry.created_at,
            payload=payload_dict,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "status": stmt.excluded.status,
                "payload": stmt.excluded.payload,
            },
        )

        with self._conn() as session:
            with session.begin():
                session.execute(stmt)

    def get(self, inquiry_id: str) -> Optional[Inquiry]:
        with self._conn() as session:
            stmt = select(InquiryModel.payload).where(InquiryModel.id == inquiry_id)
            payload = session.execute(stmt).scalar_one_or_none()
            return _deserialize(payload) if payload else None

    def get_latest_quoted(self, sender: str) -> Optional[Inquiry]:
        valid_statuses = [
            InquiryStatus.QUOTED_AUTO.value,
            InquiryStatus.QUOTED_MANUAL.value,
        ]
        with self._conn() as session:
            stmt = (
                select(InquiryModel.payload)
                .where(
                    InquiryModel.sender == sender,
                    InquiryModel.status.in_(valid_statuses),
                )
                .order_by(InquiryModel.created_at.desc())
                .limit(1)
            )
            payload = session.execute(stmt).scalar_one_or_none()
            return _deserialize(payload) if payload else None

    def get_awaiting_payment(self, sender: str) -> Optional[Inquiry]:
        """Fetches the active inquiry if the user is currently expected to send a payment receipt."""
        valid_statuses = [
            InquiryStatus.QUOTED_AUTO.value,
            InquiryStatus.QUOTED_MANUAL.value,
        ]
        with self._conn() as session:
            stmt = (
                select(InquiryModel.payload)
                .where(
                    InquiryModel.sender == sender,
                    InquiryModel.status.in_(valid_statuses),
                )
                .order_by(InquiryModel.created_at.desc())
            )
            payloads = session.execute(stmt).scalars().all()
            for payload in payloads:
                inq = _deserialize(payload)
                if inq.payment_status == PaymentStatus.PENDING:
                    return inq
            return None

    def pending_review(self) -> list[Inquiry]:
        with self._conn() as session:
            stmt = (
                select(InquiryModel.payload)
                .where(InquiryModel.status == InquiryStatus.NEEDS_REVIEW.value)
                .order_by(InquiryModel.created_at.asc())
            )
            payloads = session.execute(stmt).scalars().all()
            return [_deserialize(p) for p in payloads]

    def pending_payments(self) -> list[Inquiry]:
        """Fetches orders where the quote is sent and payment is processing verification."""
        valid_statuses = [
            InquiryStatus.QUOTED_AUTO.value,
            InquiryStatus.QUOTED_MANUAL.value,
        ]
        with self._conn() as session:
            stmt = (
                select(InquiryModel.payload)
                .where(InquiryModel.status.in_(valid_statuses))
                .order_by(InquiryModel.created_at.asc())
            )
            payloads = session.execute(stmt).scalars().all()
            
            results = []
            for p in payloads:
                inq = _deserialize(p)
                # CHANGE: Only return PROCESSING. Do not return PENDING.
                if inq.payment_status == PaymentStatus.PROCESSING:
                    results.append(inq)
            return results

    def stats(self) -> dict:
        with self._conn() as session:
            stmt = (
                select(InquiryModel.status, func.count(InquiryModel.id))
                .group_by(InquiryModel.status)
            )
            return {status: count for status, count in session.execute(stmt).all()}

    # ---- Webhook Deduplication ----

    def mark_seen(self, channel_message_id: str) -> bool:
        """Returns True if this message is new; False if already processed.

        Kept for backward compatibility / other callers. New webhook code
        should use mark_seen_and_enqueue instead, which does dedupe +
        job creation atomically in one transaction.
        """
        if not channel_message_id:
            return True

        stmt = pg_insert(SeenMessageModel).values(
            channel_message_id=channel_message_id,
            seen_at=time.time(),
        ).on_conflict_do_nothing(
            index_elements=["channel_message_id"]
        ).returning(SeenMessageModel.channel_message_id)

        with self._conn() as session:
            with session.begin():
                result = session.execute(stmt)
                return result.scalar() is not None

    # ---- Durable job queue ----

    def mark_seen_and_enqueue(self, channel_message_id: str, raw_payload: dict) -> bool:
        """Atomically dedupes and enqueues in a single transaction.

        Prevents the gap where a message is marked seen but the process
        dies before a job is created for it (which would silently drop
        the message forever, since Meta won't resend once it sees a 200).

        Returns True if this was a new message and a job was created.
        """
        if not channel_message_id:
            # No id to dedupe on -- enqueue anyway, can't safely skip.
            with self._conn() as session:
                with session.begin():
                    session.add(JobModel(payload=raw_payload, status="pending"))
            return True

        seen_stmt = pg_insert(SeenMessageModel).values(
            channel_message_id=channel_message_id,
            seen_at=time.time(),
        ).on_conflict_do_nothing(
            index_elements=["channel_message_id"]
        ).returning(SeenMessageModel.channel_message_id)

        with self._conn() as session:
            with session.begin():
                result = session.execute(seen_stmt)
                is_new = result.scalar() is not None
                if is_new:
                    session.add(JobModel(payload=raw_payload, status="pending"))
                return is_new

    def claim_next_job(self) -> Optional[dict]:
        """Atomically claims one pending (or ready-to-retry) job.

        Locks the candidate row with FOR UPDATE SKIP LOCKED (so multiple
        worker threads/processes never claim the same row), then updates
        it and returns the payload in a single round trip via RETURNING.
        Returns {"id": int, "payload": dict, "attempts": int,
        "max_attempts": int} or None if nothing is ready.
        """
        now = time.time()
        with self._conn() as session:
            with session.begin():
                subq = (
                    select(JobModel.id)
                    .where(
                        JobModel.status == "pending",
                        (JobModel.retry_at.is_(None)) | (JobModel.retry_at <= now),
                    )
                    .order_by(JobModel.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                stmt = (
                    update(JobModel)
                    .where(JobModel.id.in_(subq))
                    .values(status="processing", claimed_at=now)
                    .returning(
                        JobModel.id,
                        JobModel.payload,
                        JobModel.attempts,
                        JobModel.max_attempts,
                    )
                )
                row = session.execute(stmt).first()
                if row is None:
                    return None
                return {
                    "id": row.id,
                    "payload": row.payload,
                    "attempts": row.attempts,
                    "max_attempts": row.max_attempts,
                }

    def complete_job(self, job_id: int) -> None:
        with self._conn() as session:
            with session.begin():
                session.execute(
                    update(JobModel).where(JobModel.id == job_id).values(status="done")
                )

    def fail_job(self, job_id: int, error: str) -> None:
        """Marks a job failed and calculates exponential backoff."""
        with self._conn() as session:
            with session.begin():
                job = session.get(JobModel, job_id)
                if job is None:
                    return
                
                job.attempts += 1
                job.error = error
                
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                else:
                    job.status = "pending"
                    # Exponential backoff: 15s, 45s, 135s...
                    backoff_seconds = 15 * (3 ** (job.attempts - 1))
                    job.retry_at = time.time() + backoff_seconds

    def requeue_stuck_jobs(self, stuck_after_seconds: int = 300) -> int:
        """Requeues jobs stuck in 'processing' past the timeout -- covers
        the case where a worker crashed mid-job and never called
        complete_job/fail_job. Call this periodically from a sweeper loop.
        """
        cutoff = time.time() - stuck_after_seconds
        with self._conn() as session:
            with session.begin():
                result = session.execute(
                    update(JobModel)
                    .where(JobModel.status == "processing", JobModel.claimed_at < cutoff)
                    .values(status="pending", claimed_at=None)
                )
                return result.rowcount

    def job_stats(self) -> dict:
        with self._conn() as session:
            stmt = (
                select(JobModel.status, func.count(JobModel.id))
                .group_by(JobModel.status)
            )
            return {status: count for status, count in session.execute(stmt).all()}