"""Core domain models. Pure Python, no framework imports.

These are the vocabulary of the whole system. Every layer speaks in
these types; nothing here knows about WhatsApp, Claude, CSV or SQLite.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pydantic import BaseModel

class MediaKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"
    DOCX = "docx"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class IncomingMessage:
    """A normalized inbound message, regardless of channel."""
    sender: str                     # e.g. WhatsApp phone number "2348012345678"
    sender_name: str
    kind: MediaKind
    text: Optional[str] = None      # body text or caption
    media_bytes: Optional[bytes] = None
    media_mime: Optional[str] = None
    channel_message_id: str = ""


@dataclass(frozen=True)
class ExtractedBook:
    """One line item as read off the school book list."""
    title: str
    author_or_publisher: Optional[str] = None
    grade: Optional[str] = None     # e.g. "SS1", "Primary 4"
    quantity: int = 1


@dataclass(frozen=True)
class InventoryItem:
    sku: str
    title: str
    author_or_publisher: str
    price: float                    # in Naira
    stock: int

    @property
    def in_stock(self) -> bool:
        return self.stock > 0


@dataclass(frozen=True)
class MatchCandidate:
    item: InventoryItem
    score: float                    # 0-100


@dataclass
class MatchResult:
    requested: ExtractedBook
    best: Optional[MatchCandidate]          # None => no plausible match at all
    alternatives: list[MatchCandidate] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.best is not None and self.best.score >= 100  # overridden by matcher config


@dataclass(frozen=True)
class QuoteLine:
    requested_title: str
    quantity: int
    matched: Optional[InventoryItem]        # None => "not found"
    confidence: float                        # 0 when unmatched

    @property
    def line_total(self) -> float:
        if self.matched is None or not self.matched.in_stock:
            return 0.0
        return self.matched.price * self.quantity


@dataclass
class Quote:
    lines: list[QuoteLine]

    @property
    def total(self) -> float:
        return sum(l.line_total for l in self.lines)

    @property
    def unmatched(self) -> list[QuoteLine]:
        return [l for l in self.lines if l.matched is None]

    @property
    def out_of_stock(self) -> list[QuoteLine]:
        return [l for l in self.lines if l.matched is not None and not l.matched.in_stock]


class InquiryStatus(str, Enum):
    RECEIVED = "received"
    EXTRACTED = "extracted"
    QUOTED_AUTO = "quoted_auto"          # quote sent automatically
    NEEDS_REVIEW = "needs_review"        # waiting for a human in the dashboard
    QUOTED_MANUAL = "quoted_manual"      # human approved & sent
    CONFIRMED = "confirmed"              # user replied YES
    FAILED = "failed"

@dataclass
class Inquiry:
    """The unit of work: one inbound book list, end to end."""
    sender: str
    sender_name: str
    status: InquiryStatus = InquiryStatus.RECEIVED
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    raw_text: Optional[str] = None
    extracted: list[ExtractedBook] = field(default_factory=list)
    matches: list[MatchResult] = field(default_factory=list)
    quote: Optional[Quote] = None
    revised_quote: Optional[Quote] = None
    error: Optional[str] = None

class DashboardLineItem(BaseModel):
    sku: str
    title: str
    quantity: int
    override_price: float

class ManualReviewSubmission(BaseModel):
    lines: list[DashboardLineItem]