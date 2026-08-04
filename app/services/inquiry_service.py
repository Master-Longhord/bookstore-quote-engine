"""The use-case orchestrator: one inbound message -> quote or review queue.

This is the only place the pipeline order lives. It depends exclusively
on the domain ports (Dependency Inversion), so every collaborator can be
faked in tests and swapped in production without touching this file.
"""
from __future__ import annotations

import base64
import logging
import time

from app.domain.interfaces import (
    BookMatcher,
    DocumentExtractor,
    InquiryStore,
    InventoryRepository,
    MessagingClient,
    QuoteRenderer,
)
from app.domain.models import (
    ExtractedBook,
    IncomingMessage,
    Inquiry,
    InquiryStatus,
    InventoryItem,
    ManualReviewSubmission,
    MediaKind,
    Quote,
    QuoteLine,
    PaymentStatus,
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
    "Welcome to the BookDepot! To get an instant quote, please send a "
    "clear photo, a PDF, or type out your school's book list.\n\n"
    "For the most accurate results when typing, please list each book on a new line like this:\n\n"
    "Example:\n"
    "First Steps in Reading Book 1\n"
    "Integrated Science for Jamaica Grade 7\n"
    "Primary Mathematics (Qty: 2)\n\n"
    "*(If you have a specific question not covered by our menu, please contact our office at (876) 619-8419 for further assistance!)*"
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
        inventory: InventoryRepository,
        messenger: MessagingClient,
        renderer: QuoteRenderer,
        auto_send_threshold: float,
    ):
        self._extractor = extractor
        self._matcher = matcher
        self._store = store
        self._inventory = inventory
        self._messenger = messenger
        self._renderer = renderer
        self._builder = QuoteBuilder()
        self._pdf_generator = PdfGenerator()
        self._threshold = auto_send_threshold

    # ---- inbound path ----

    def handle_message(self, message: IncomingMessage, is_last_attempt: bool = False) -> Inquiry:
        text_content = message.text.strip() if message.text else ""
        upper_text = text_content.upper()
        
        # --- 1. PAYMENT & CONFIRMATION INTERCEPTOR ---
        active_quote = self._store.get_latest_quoted(message.sender)
        
        if active_quote and active_quote.payment_status == PaymentStatus.PENDING:
            
            # Scenario A: Customer explicitly cancels the quote
            if upper_text in ("NO", "CANCEL", "STOP"):
                active_quote.status = InquiryStatus.FAILED
                active_quote.error = "cancelled by user"
                self._store.save(active_quote)
                self._messenger.send_text(
                    message.sender, 
                    "Quote cancelled. You can send a new book list whenever you're ready!"
                )
                return active_quote

            # Scenario B: Customer confirms they want to pay
            if upper_text == "YES":
                active_quote_data = active_quote.revised_quote or active_quote.quote
                formatted_total = f"J${active_quote_data.total:,.2f}"
                
                custom_bank_message = (
                    f"Order Ref: #{active_quote.id[:8]}\n"
                    "*(Disclaimer: This is not a confirmed order until payment is recieved)*\n"
                    f"Total Amount Due: *{formatted_total}*\n\n"
                    "Please make a transfer to the following account:\n"
                    "• Bank: National Commercial Bank\n"
                    "• Branch: Duke Street\n"
                    "• Account Number: 065608154\n"
                    "• Account Type: Savings\n"
                    "• Account Name: Book Depot Limited\n\n"
                    f"Once paid, please reply with the ACCOUNT NAME you transferred from and the AMOUNT sent, or upload a screenshot of your receipt. Please include your Order Ref (*#{active_quote.id[:8]}*)."
                )
                
                # Hidden state trick: mark that they have initiated payment
                active_quote.raw_text = (active_quote.raw_text or "") + "\n[AWAITING_RECEIPT]"
                self._store.save(active_quote)
                self._messenger.send_text(message.sender, custom_bank_message)
                return active_quote

            # Scenario C: Catching the actual receipt (ONLY if they previously said YES)
            is_awaiting_receipt = "[AWAITING_RECEIPT]" in (active_quote.raw_text or "")
            
            if is_awaiting_receipt:
                is_valid_note = len(text_content) > 5 and upper_text not in ("HELLO", "THANKS", "THANK YOU", "OKAY", "SURE")
                
                if message.media_bytes or is_valid_note:
                    active_quote.payment_status = PaymentStatus.PROCESSING
                    
                    if message.media_bytes:
                        encoded = base64.b64encode(message.media_bytes).decode("utf-8")
                        mime_type = message.media_mime or "image/jpeg"
                        active_quote.payment_receipt_base64 = f"data:{mime_type};base64,{encoded}"
                    
                    if text_content:
                        note = f"\n[Payment Note: {text_content}]"
                        active_quote.raw_text = (active_quote.raw_text or "") + note
                        
                    self._store.save(active_quote)
                    
                    self._messenger.send_text(
                        message.sender,
                        "Thank you! We have received your payment submission. Our team will verify it shortly and process your final receipt."
                    )
                    return active_quote
            
            # If they have a pending quote but just sent an image/text without saying YES or NO,
            # we simply do nothing here. The code will fall through to Step 2 and treat it as a new book list.


        # --- 2. NORMAL NEW INQUIRY PATH ---
        inquiry = Inquiry(
            sender=message.sender,
            sender_name=message.sender_name,
            raw_text=message.text,
        )

        # Reject unsupported files (audio, video, stickers)
        if message.kind == MediaKind.UNSUPPORTED:
            self._messenger.send_text(message.sender, UNSUPPORTED_REPLY)
            inquiry.status = InquiryStatus.FAILED
            inquiry.error = "unsupported media type"
            self._store.save(inquiry)
            return inquiry

        # Proceed to filter out normal short text (like "Hi", "Hello")
        if message.kind == MediaKind.TEXT:
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
        except Exception as exc: 
            if not is_last_attempt:
                # Bubble the error up to main.py so the queue triggers a retry
                raise 
                
            # If we reach here, we have exhausted all retries. Fail gracefully.
            log.exception("Extraction failed permanently for %s", inquiry.id)
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

        # FORCE ALL QUOTES TO MANUAL REVIEW (Skipping _all_confident check)
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
                        
                    # --- QUARANTINE ZONE: Matcher Check & Save ---
                    dummy_req = ExtractedBook(title=custom_title, quantity=1)
                    match_res = self._matcher.match(dummy_req)
                    
                    if match_res.best and match_res.best.score >= 95.0:
                        # Found a near-perfect match in the database already, link it
                        matched = match_res.best.item
                    else:
                        # Truly missing. Create AUTO-ADD SKU and save it.
                        auto_sku = f"AUTO-ADD-{int(time.time())}-{idx}"
                        matched = InventoryItem(
                            sku=auto_sku,
                            title=custom_title,
                            author_or_publisher="Manual Entry",
                            price=custom_price,
                            stock=999,
                        )
                        self._inventory.add_item(matched)
                    # ---------------------------------------------
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
        for idx, line_item in enumerate(payload.lines):
            sku = line_item.sku
            
            # If SKU is empty or explicitly custom, process via Quarantine Zone
            if not sku or sku.startswith("manual-override") or sku.startswith("CUSTOM"):
                dummy_req = ExtractedBook(title=line_item.title, quantity=1)
                match_res = self._matcher.match(dummy_req)
                
                if match_res.best and match_res.best.score >= 95.0:
                    item = match_res.best.item
                else:
                    auto_sku = f"AUTO-ADD-{int(time.time())}-{idx}"
                    item = InventoryItem(
                        sku=auto_sku,
                        title=line_item.title,
                        author_or_publisher="Manual Entry",
                        price=line_item.override_price,
                        stock=999,
                    )
                    self._inventory.add_item(item)
            else:
                # Existing item behavior
                item = InventoryItem(
                    sku=sku,
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