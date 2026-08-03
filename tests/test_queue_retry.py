from __future__ import annotations
import sys
from unittest.mock import MagicMock

# --- MOCK OUT WEASYPRINT BEFORE ANY APP IMPORTS ---
mock_weasyprint = MagicMock()
sys.modules["weasyprint"] = mock_weasyprint

import unittest
from app.services.inquiry_service import InquiryService
from app.domain.models import IncomingMessage, MediaKind, InquiryStatus

class TestQueueRetry(unittest.TestCase):
    def test_extraction_retry_and_recovery(self):
        """Test that transient extraction errors trigger retries, 
        and success on a later attempt completes the job.
        """
        # 1. Arrange mocks
        mock_extractor = MagicMock()
        
        # Use a dynamic side effect function to guarantee exceptions are raised on calls 1 and 2
        call_count = {"val": 0}
        def mock_extract_side_effect(msg):
            call_count["val"] += 1
            if call_count["val"] == 1:
                raise Exception("API Timeout")
            elif call_count["val"] == 2:
                raise Exception("Rate Limit 503")
            return [MagicMock(title="Test Book", quantity=1)]

        mock_extractor.extract.side_effect = mock_extract_side_effect
        
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = MagicMock(best=None, alternatives=[])
        
        mock_store = MagicMock()
        mock_messenger = MagicMock()
        mock_renderer = MagicMock()
        
        service = InquiryService(
            extractor=mock_extractor,
            matcher=mock_matcher,
            store=mock_store,
            messenger=mock_messenger,
            renderer=mock_renderer,
            auto_send_threshold=80.0
        )
        
        message = IncomingMessage(
            sender="18765550199",
            sender_name="Test User",
            kind=MediaKind.TEXT,
            text="First Steps in Reading Book 1 for Grade 2 student"
        )

        # 2. Attempt 1: Should fail and raise "API Timeout"
        with self.assertRaisesRegex(Exception, "API Timeout"):
            service.handle_message(message, is_last_attempt=False)
        
        # 3. Attempt 2: Should fail and raise "Rate Limit 503"
        with self.assertRaisesRegex(Exception, "Rate Limit 503"):
            service.handle_message(message, is_last_attempt=False)

        # 4. Attempt 3: Should succeed and process normally
        inquiry = service.handle_message(message, is_last_attempt=True)
        
        self.assertIn(inquiry.status, (InquiryStatus.NEEDS_REVIEW, InquiryStatus.QUOTED_AUTO))
        self.assertEqual(call_count["val"], 3)

if __name__ == "__main__":
    unittest.main()