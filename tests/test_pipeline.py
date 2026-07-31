"""Offline tests: no network, no Claude, no WhatsApp.

Fakes implement the same ports as production adapters — which is exactly
the payoff of the interfaces (Liskov + Dependency Inversion in action).

Run:  python -m unittest discover tests -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from app.adapters.csv_inventory import CsvInventoryRepository
from app.adapters.fuzzy_matcher import FuzzyBookMatcher, normalize
from app.adapters.sqlite_store import SqliteInquiryStore
from app.domain.models import (
    ExtractedBook,
    IncomingMessage,
    InquiryStatus,
    MediaKind,
)
from app.services.inquiry_service import InquiryService
from app.services.quote_service import QuoteBuilder, WhatsAppQuoteRenderer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(HERE, "data", "inventory.csv")


class FakeExtractor:
    def __init__(self, books):
        self.books = books

    def extract(self, message):
        return self.books


class FakeMessenger:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send_text(self, to, body):
        self.sent.append((to, body))


def make_service(books, threshold=88.0):
    inventory = CsvInventoryRepository(CSV)
    matcher = FuzzyBookMatcher(inventory, confident_threshold=threshold)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    store = SqliteInquiryStore(tmp.name)
    messenger = FakeMessenger()
    service = InquiryService(
        extractor=FakeExtractor(books),
        matcher=matcher,
        store=store,
        messenger=messenger,
        renderer=WhatsAppQuoteRenderer("Test Books"),
        auto_send_threshold=threshold,
    )
    return service, store, messenger


def msg(text="here is my list"):
    return IncomingMessage(
        sender="2348012345678",
        sender_name="Mrs Ade",
        kind=MediaKind.TEXT,
        text=text,
        channel_message_id="wamid.test1",
    )


class TestNormalize(unittest.TestCase):
    def test_expansions(self):
        self.assertEqual(
            normalize("New Gen. Maths SS1"),
            "new general mathematics senior secondary 1",
        )
        self.assertIn("junior secondary 2", normalize("Eng Lang JSS2"))


class TestMatcher(unittest.TestCase):
    def setUp(self):
        self.matcher = FuzzyBookMatcher(CsvInventoryRepository(CSV))

    def test_abbreviated_title_matches(self):
        r = self.matcher.match(ExtractedBook(title="New Gen Maths", grade="SS1"))
        self.assertIsNotNone(r.best)
        self.assertEqual(r.best.item.sku, "NGM-SS1")
        self.assertTrue(self.matcher.is_confident(r))

    def test_grade_disambiguates_volumes(self):
        r = self.matcher.match(ExtractedBook(title="New General Mathematics", grade="SS2"))
        self.assertEqual(r.best.item.sku, "NGM-SS2")

    def test_nonsense_gets_no_match(self):
        r = self.matcher.match(ExtractedBook(title="Advanced Quantum Chromodynamics Vol 9"))
        self.assertIsNone(r.best)


class TestQuote(unittest.TestCase):
    def test_totals_skip_unmatched_and_out_of_stock(self):
        inv = CsvInventoryRepository(CSV)
        m = FuzzyBookMatcher(inv)
        matches = [
            m.match(ExtractedBook(title="New Gen Maths SS1", quantity=2)),   # 2 x 4500
            m.match(ExtractedBook(title="New School Chemistry SS1")),        # out of stock
            m.match(ExtractedBook(title="Totally Unknown Book Xyz")),        # unmatched
        ]
        quote = QuoteBuilder().build(matches)
        self.assertEqual(quote.total, 9000)
        self.assertEqual(len(quote.unmatched), 1)
        self.assertEqual(len(quote.out_of_stock), 1)


class TestOrchestration(unittest.TestCase):
    def test_confident_list_auto_quotes(self):
        service, store, messenger = make_service(
            [
                ExtractedBook(title="New General Mathematics SS1"),
                ExtractedBook(title="Without a Silver Spoon"),
            ]
        )
        inquiry = service.handle_message(msg())
        self.assertEqual(inquiry.status, InquiryStatus.QUOTED_AUTO)
        self.assertEqual(len(messenger.sent), 1)
        body = messenger.sent[0][1]
        self.assertIn("Total", body)
        self.assertIn("\u20a6", body)
        # persisted round-trip
        self.assertEqual(store.get(inquiry.id).status, InquiryStatus.QUOTED_AUTO)

    def test_fuzzy_list_goes_to_review_then_approval_sends(self):
        service, store, messenger = make_service(
            [
                ExtractedBook(title="New Gen Maths SS1"),
                ExtractedBook(title="Some Mystery Reader P4"),  # low confidence
            ]
        )
        inquiry = service.handle_message(msg())
        self.assertEqual(inquiry.status, InquiryStatus.NEEDS_REVIEW)
        self.assertEqual(len(store.pending_review()), 1)
        self.assertIn("double-checking", messenger.sent[0][1])

        # Shop owner marks line 1 as unavailable and approves.
        approved = service.approve(inquiry.id, {1: None})
        self.assertEqual(approved.status, InquiryStatus.QUOTED_MANUAL)
        self.assertEqual(len(messenger.sent), 2)
        self.assertIn("Total", messenger.sent[1][1])
        self.assertEqual(len(store.pending_review()), 0)

    def test_empty_extraction_gets_polite_reply(self):
        service, _, messenger = make_service([])
        inquiry = service.handle_message(msg("hello"))
        self.assertEqual(inquiry.status, InquiryStatus.FAILED)
        self.assertIn("book list", messenger.sent[0][1])


class TestStoreDedup(unittest.TestCase):
    def test_mark_seen(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        store = SqliteInquiryStore(tmp.name)
        self.assertTrue(store.mark_seen("wamid.abc"))
        self.assertFalse(store.mark_seen("wamid.abc"))


if __name__ == "__main__":
    unittest.main()
