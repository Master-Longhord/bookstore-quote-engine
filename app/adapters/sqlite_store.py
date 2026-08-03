"""InquiryStore backed by SQLite — perfect for a $5 VPS / Railway volume.

Inquiries are stored as a small set of indexed columns plus a JSON blob
of the full domain object, so the schema never fights the domain model.
Also tracks processed WhatsApp message IDs, because Meta redelivers
webhooks on any non-200 and duplicates would double-quote customers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from typing import Optional

from app.domain.models import (
    ExtractedBook,
    Inquiry,
    InquiryStatus,
    InventoryItem,
    MatchCandidate,
    MatchResult,
    Quote,
    QuoteLine,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inquiries (
    id         TEXT PRIMARY KEY,
    sender     TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at REAL NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status);
CREATE TABLE IF NOT EXISTS seen_messages (
    channel_message_id TEXT PRIMARY KEY,
    seen_at            REAL NOT NULL DEFAULT (unixepoch())
);
"""


class SqliteInquiryStore:
    def __init__(self, db_path: str):
        self._path = db_path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- InquiryStore port ----

    def save(self, inquiry: Inquiry) -> None:
        payload = json.dumps(_serialize(inquiry), ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO inquiries(id, sender, status, created_at, payload) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "payload=excluded.payload",
                (inquiry.id, inquiry.sender, inquiry.status.value,
                 inquiry.created_at, payload),
            )

    def get(self, inquiry_id: str) -> Optional[Inquiry]:
        with self._conn() as c:
            row = c.execute(
                "SELECT payload FROM inquiries WHERE id=?", (inquiry_id,)
            ).fetchone()
        return _deserialize(json.loads(row["payload"])) if row else None

    def get_latest_quoted(self, sender: str) -> Optional[Inquiry]:
        with self._conn() as c:
            row = c.execute(
                "SELECT payload FROM inquiries "
                "WHERE sender=? AND status IN (?, ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (sender, InquiryStatus.QUOTED_AUTO.value, InquiryStatus.QUOTED_MANUAL.value)
            ).fetchone()
        return _deserialize(json.loads(row["payload"])) if row else None
    
    def pending_review(self) -> list[Inquiry]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT payload FROM inquiries WHERE status=? ORDER BY created_at",
                (InquiryStatus.NEEDS_REVIEW.value,),
            ).fetchall()
        return [_deserialize(json.loads(r["payload"])) for r in rows]

    def stats(self) -> dict:
        with self._conn() as c:
            rows = c.execute(
                "SELECT status, COUNT(*) n FROM inquiries GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ---- webhook dedup (Meta redelivers on non-200) ----

    def mark_seen(self, channel_message_id: str) -> bool:
        """Returns True if this message is new; False if already processed."""
        if not channel_message_id:
            return True
        with self._lock, self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO seen_messages(channel_message_id) VALUES(?)",
                    (channel_message_id,),
                )
                return True
            except sqlite3.IntegrityError:
                return False


# ---- (de)serialization helpers ----

def _serialize(inq: Inquiry) -> dict:
    return asdict(inq) | {"status": inq.status.value}


def _deserialize(d: dict) -> Inquiry:
    def item(x: Optional[dict]) -> Optional[InventoryItem]:
        return InventoryItem(**x) if x else None

    def cand(x: Optional[dict]) -> Optional[MatchCandidate]:
        return MatchCandidate(item=item(x["item"]), score=x["score"]) if x else None

    def parse_quote(q_dict: Optional[dict]) -> Optional[Quote]:
        if not q_dict:
            return None
        return Quote(
            lines=[
                QuoteLine(
                    requested_title=l["requested_title"],
                    quantity=l["quantity"],
                    matched=item(l.get("matched")),
                    confidence=l["confidence"],
                )
                for l in q_dict["lines"]
            ]
        )

    extracted = [ExtractedBook(**b) for b in d.get("extracted", [])]
    matches = [
        MatchResult(
            requested=ExtractedBook(**m["requested"]),
            best=cand(m.get("best")),
            alternatives=[cand(a) for a in m.get("alternatives", [])],
        )
        for m in d.get("matches", [])
    ]
    
    return Inquiry(
        id=d["id"],
        sender=d["sender"],
        sender_name=d.get("sender_name", ""),
        status=InquiryStatus(d["status"]),
        created_at=d["created_at"],
        raw_text=d.get("raw_text"),
        extracted=extracted,
        matches=matches,
        quote=parse_quote(d.get("quote")),
        revised_quote=parse_quote(d.get("revised_quote")),
        error=d.get("error"),
        claimed_by=d.get("claimed_by"),
        claimed_at=d.get("claimed_at"),
    )