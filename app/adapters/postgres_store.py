"""InquiryStore adapter backed by PostgreSQL and synchronous SQLAlchemy.

Maintains the exact same synchronous interface as SqliteInquiryStore,
allowing a friction-free drop-in replacement while providing Postgres stability.
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import Float, String, select, func, create_engine
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.adapters.sqlite_store import _deserialize, _serialize
from app.domain.models import Inquiry, InquiryStatus

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


# ---- Postgres Inquiry Store (Synchronous) ----

class PostgresInquiryStore:
    def __init__(self, database_url: str):
        # Create a synchronous connection engine
        self._engine = create_engine(database_url, pool_pre_ping=True)
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
        with self._conn() as session:
            stmt = (
                select(InquiryModel.payload)
                .where(
                    InquiryModel.sender == sender,
                    InquiryModel.status == InquiryStatus.AWAITING_PAYMENT.value,
                )
                .order_by(InquiryModel.created_at.desc())
                .limit(1)
            )
            payload = session.execute(stmt).scalar_one_or_none()
            return _deserialize(payload) if payload else None

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
        """Fetches orders where the customer has submitted payment proof."""
        with self._conn() as session:
            stmt = (
                select(InquiryModel.payload)
                .where(InquiryModel.status == InquiryStatus.NEEDS_PAYMENT_REVIEW.value)
                .order_by(InquiryModel.created_at.asc())
            )
            payloads = session.execute(stmt).scalars().all()
            return [_deserialize(p) for p in payloads]

    def stats(self) -> dict:
        with self._conn() as session:
            stmt = (
                select(InquiryModel.status, func.count(InquiryModel.id))
                .group_by(InquiryModel.status)
            )
            return {status: count for status, count in session.execute(stmt).all()}

    # ---- Webhook Deduplication ----

    def mark_seen(self, channel_message_id: str) -> bool:
        """Returns True if this message is new; False if already processed."""
        if not channel_message_id:
            return True

        # Add the .returning() clause to explicitly fetch the inserted ID
        stmt = pg_insert(SeenMessageModel).values(
            channel_message_id=channel_message_id,
            seen_at=time.time(),
        ).on_conflict_do_nothing(
            index_elements=["channel_message_id"]
        ).returning(SeenMessageModel.channel_message_id)

        with self._conn() as session:
            with session.begin():
                result = session.execute(stmt)
                # scalar() fetches the first column of the first row, or None if empty
                return result.scalar() is not None