"""The use-case orchestrator: one inbound message -> quote or review queue.

This is the only place the pipeline order lives. It depends exclusively
on the domain ports (Dependency Inversion), so every collaborator can be
faked in tests and swapped in production without touching this file.
"""
from __future__ import annotations

import logging

from app.domain.interfaces import (
    BookMatcher,
    DocumentExtractor,
    InquiryStore,
    MessagingClient,
    QuoteRenderer,
)
from app.domain.models import (
    IncomingMessage,
    Inquiry,
    InquiryStatus,
    InventoryItem,
    ManualReviewSubmission,
    MediaKind,
    Quote,
    QuoteLine,
)
from app.services.quote_service import QuoteBuilder
from app.services.pdf_service import PdfGenerator

log = logging.getLogger(__name__)

UNSUPPORTED_REPLY = (
    "Thanks for reaching out! Please send your book list as a *photo*, "
    "*PDF*, *Word document*, or just type it out, and we'll send you a "
    "quote right away."
)
EMPTY_REPLY = (
    "Welcome to the bookstore! To get an instant quote, please send a "
    "clear photo, a PDF, or type out your school's book list.\n\n"
    "For the most accurate results when typing, please list each book on a new line like this:\n\n"
    "Example:\n"
    "First Steps in Reading Book 1\n"
    "Integrated Science for Jamaica Grade 7\n"
    "Primary Mathematics (Qty: 2)"
)
REVIEW_REPLY = (
    "Thank you{name}! We've received your book list. A member of our "
    "team is double-checking a few titles and your quote will arrive "
    "here shortly."
)
ERROR_REPLY = (
    "Sorry, we had trouble reading that document. Could you try sending "
    "it again, a clear photo or PDF works best."
)


class InquiryService:
    def __init__(
        self,
        extractor: DocumentExtractor,
        matcher: BookMatcher,
        store: InquiryStore,
        messenger: MessagingClient,
        renderer: QuoteRenderer,
        auto_send_threshold: float,
    ):
        self._extractor = extractor
        self._matcher = matcher
        self._store = store
        self._messenger = messenger
        self._renderer = renderer
        self._builder = QuoteBuilder()
        self._pdf_generator = PdfGenerator()
        self._threshold = auto_send_threshold

    # ---- inbound path ----

    def handle_message(self, message: IncomingMessage) -> Inquiry:
        inquiry = Inquiry(
            sender=message.sender,
            sender_name=message.sender_name,
            raw_text=message.text,
        )

        # 1. Reject unsupported files (audio, video, stickers)
        if message.kind == MediaKind.UNSUPPORTED:
            self._messenger.send_text(message.sender, UNSUPPORTED_REPLY)
            inquiry.status = InquiryStatus.FAILED
            inquiry.error = "unsupported media type"
            self._store.save(inquiry)
            return inquiry

        # 2. Filter out short text greetings or intercept "YES"
        if message.kind == MediaKind.TEXT:
            text_content = message.text.strip() if message.text else ""
            
            # Intercept the confirmation command
            if text_content.upper() == "YES":
                recent_quote = self._store.get_latest_quoted(message.sender)
                if recent_quote and (recent_quote.quote or recent_quote.revised_quote):
                    recent_quote.status = InquiryStatus.CONFIRMED
                    self._store.save(recent_quote)
                    
                    self._messenger.send_text(
                        message.sender,
                        "Thank you for shopping with us! Your order is confirmed. Generating your document now..."
                    )
                    
                    try:
                        # Use revised_quote if it exists, otherwise fall back to original quote
                        active_quote = recent_quote.revised_quote or recent_quote.quote
                        pdf_bytes = self._pdf_generator.generate_quote_pdf(recent_quote, active_quote)
                        self._messenger.send_document(
                            to=message.sender,
                            document_bytes=pdf_bytes,
                            filename="Bookstore_Quote.pdf"
                        )
                    except Exception as exc:
                        log.exception("PDF generation or sending failed for %s: %s", recent_quote.id, exc)
                        self._messenger.send_text(
                            message.sender,
                            "We confirmed your order, but there was an error generating the PDF receipt. A human agent will contact you shortly."
                        )
                        
                    return recent_quote
                else:
                    self._messenger.send_text(
                        message.sender,
                        "We couldn't find an active quote to confirm. Please send your book list again."
                    )
                    return inquiry

            # Proceed to filter out normal short text (like "Hi", "Hello")
            if len(text_content) < 15:
                self._messenger.send_text(message.sender, EMPTY_REPLY)
                inquiry.status = InquiryStatus.FAILED
                inquiry.error = "text too short to be a book list"
                self._store.save(inquiry)
                return inquiry

        # 3. Proceed to Extraction for images, PDFs, and long text
        try:
            inquiry.extracted = self._extractor.extract(message)
            inquiry.status = InquiryStatus.EXTRACTED
        except Exception as exc:  # extraction is the flaky boundary
            log.exception("Extraction failed for %s", inquiry.id)
            inquiry.status = InquiryStatus.FAILED
            inquiry.error = f"extraction: {exc}"
            self._store.save(inquiry)
            self._messenger.send_text(message.sender, ERROR_REPLY)
            return inquiry

        if not inquiry.extracted:
            inquiry.status = InquiryStatus.FAILED
            inquiry.error = "no books found in message"
            self._store.save(inquiry)
            self._messenger.send_text(message.sender, EMPTY_REPLY)
            return inquiry

        inquiry.matches = [self._matcher.match(b) for b in inquiry.extracted]
        inquiry.quote = self._builder.build(inquiry.matches)

        if self._all_confident(inquiry):
            body = self._renderer.render(inquiry, inquiry.quote)
            self._messenger.send_text(inquiry.sender, body)
            inquiry.status = InquiryStatus.QUOTED_AUTO
        else:
            name = f" {inquiry.sender_name}" if inquiry.sender_name else ""
            self._messenger.send_text(
                inquiry.sender, REVIEW_REPLY.format(name=name)
            )
            inquiry.status = InquiryStatus.NEEDS_REVIEW

        self._store.save(inquiry)
        return inquiry

    # ---- review path (dashboard actions) ----

    def approve(self, inquiry_id: str, corrections: dict[int, str | None]) -> Inquiry:
        """Apply human corrections and send the quote."""
        inquiry = self._store.get(inquiry_id)
        if inquiry is None:
            raise KeyError(inquiry_id)
        if inquiry.quote is None:
            raise ValueError(f"Inquiry {inquiry_id} has no quote to approve")

        new_lines: list[QuoteLine] = []
        for idx, line in enumerate(inquiry.quote.lines):
            if idx in corrections:
                sku_or_custom = corrections[idx]
                
                if not sku_or_custom:
                    matched = None
                elif sku_or_custom.startswith("CUSTOM::"):
                    # Unpack the contract from routes.py
                    parts = sku_or_custom.split("::")
                    custom_title = parts[1]
                    try:
                        custom_price = float(parts[2])
                    except ValueError:
                        custom_price = 0.0
                        
                    matched = InventoryItem(
                        sku=f"manual-override-{idx}",
                        title=custom_title,
                        author_or_publisher="Manual Entry",
                        price=custom_price,
                        stock=999,
                    )
                else:
                    # Proceed with normal CSV/DB SKU lookup
                    matched = self._lookup(inquiry, sku_or_custom)

                new_lines.append(
                    QuoteLine(
                        requested_title=line.requested_title,
                        quantity=line.quantity,
                        matched=matched,
                        confidence=100.0 if matched else 0.0,
                    )
                )
            else:
                new_lines.append(line)
        
        # Preserve original AI quote, assign updates to revised_quote
        revised_quote = Quote(lines=new_lines)
        inquiry.revised_quote = revised_quote
        inquiry.status = InquiryStatus.QUOTED_MANUAL
        
        self._store.save(inquiry)

        body = self._renderer.render(inquiry, revised_quote)
        self._messenger.send_text(inquiry.sender, body)
        return inquiry

    def approve_manual_override(self, inquiry_id: str, payload: ManualReviewSubmission) -> Inquiry:
        """Processes full manual edits from the dashboard, bypassing the original matcher."""
        inquiry = self._store.get(inquiry_id)
        if not inquiry:
            raise KeyError(f"Inquiry {inquiry_id} not found")

        revised_lines: list[QuoteLine] = []
        for line_item in payload.lines:
            item = InventoryItem(
                sku=line_item.sku,
                title=line_item.title,
                author_or_publisher="Manual Entry",
                price=line_item.override_price,
                stock=999,  
            )
            
            line = QuoteLine(
                requested_title=line_item.title,
                quantity=line_item.quantity,
                matched=item,
                confidence=100.0,
            )
            revised_lines.append(line)

        revised_quote = Quote(lines=revised_lines)
        inquiry.revised_quote = revised_quote
        inquiry.status = InquiryStatus.QUOTED_MANUAL

        self._store.save(inquiry)

        message_text = self._renderer.render(inquiry, revised_quote)
        self._messenger.send_text(inquiry.sender, message_text)

        return inquiry

    # ---- internals ----

    def _all_confident(self, inquiry: Inquiry) -> bool:
        return all(
            m.best is not None and m.best.score >= self._threshold
            for m in inquiry.matches
        )

    def _lookup(self, inquiry: Inquiry, sku: str):
        # Prefer candidates already attached to the inquiry (fast, consistent),
        # fall back to nothing, the dashboard only offers known SKUs.
        for m in inquiry.matches:
            for c in ([m.best] if m.best else []) + m.alternatives:
                if c and c.item.sku == sku:
                    return c.item
        return None