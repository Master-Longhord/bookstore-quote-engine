"""Ports (interfaces) for the hexagonal architecture.

SOLID mapping:
- Dependency Inversion: services depend on these Protocols, never on
  concrete adapters (Claude, WhatsApp, CSV, SQLite).
- Interface Segregation: each port is small and single-purpose, so a
  fake/stub for tests only implements what it needs.
- Liskov Substitution: any implementation of a port is a drop-in
  replacement (e.g. swap CsvInventoryRepository for a Postgres one).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from .models import (
    ExtractedBook,
    IncomingMessage,
    InventoryItem,
    Inquiry,
    MatchResult,
    Quote,
)


@runtime_checkable
class DocumentExtractor(Protocol):
    """Turns a raw message (image / PDF / docx / text) into structured books."""

    def extract(self, message: IncomingMessage) -> list[ExtractedBook]: ...


@runtime_checkable
class InventoryRepository(Protocol):
    """Read access to the bookstore's stock."""

    def all_items(self) -> list[InventoryItem]: ...
    def get(self, sku: str) -> Optional[InventoryItem]: ...
    def refresh(self) -> None: ...   # re-read the source (CSV hot-reload)


@runtime_checkable
class BookMatcher(Protocol):
    """Maps an extracted book onto inventory items with a confidence score."""

    def match(self, book: ExtractedBook) -> MatchResult: ...


@runtime_checkable
class MessagingClient(Protocol):
    """Outbound side of the chat channel (WhatsApp, or a console fake)."""

    def send_text(self, to: str, body: str) -> None: ...


@runtime_checkable
class InquiryStore(Protocol):
    """Persistence for inquiries + the human review queue."""

    def save(self, inquiry: Inquiry) -> None: ...
    def get(self, inquiry_id: str) -> Optional[Inquiry]: ...
    def pending_review(self) -> list[Inquiry]: ...
    def stats(self) -> dict: ...
    def get_latest_quoted(self, sender: str) -> Optional[Inquiry]: ...


@runtime_checkable
class QuoteRenderer(Protocol):
    """Formats a Quote for a channel (WhatsApp text now; PDF later)."""

    def render(self, inquiry: Inquiry, quote: Quote) -> str: ...

@runtime_checkable
class MessagingClient(Protocol):
    """Outbound side of the chat channel."""

    def send_text(self, to: str, body: str) -> None: ...
    def send_document(self, to: str, document_bytes: bytes, filename: str) -> None: ...