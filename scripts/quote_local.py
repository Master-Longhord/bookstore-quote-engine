"""Test the pipeline locally without WhatsApp.

Feeds a file (image/PDF/docx) or raw text through extraction -> matching
-> quote, printing the WhatsApp message to the console. Only needs
ANTHROPIC_API_KEY set (WhatsApp vars can be dummy values).

Usage:
    ANTHROPIC_API_KEY=sk-... WA_ACCESS_TOKEN=x WA_PHONE_NUMBER_ID=x \
        python scripts/quote_local.py samples/booklist.jpg
    ... python scripts/quote_local.py --text "New Gen Maths SS1, Without a Silver Spoon"
"""
from __future__ import annotations

import mimetypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.claude_extractor import ClaudeExtractor  # noqa: E402
from app.adapters.csv_inventory import CsvInventoryRepository  # noqa: E402
from app.adapters.fuzzy_matcher import FuzzyBookMatcher  # noqa: E402
from app.adapters.sqlite_store import SqliteInquiryStore  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.domain.models import IncomingMessage, MediaKind  # noqa: E402
from app.services.inquiry_service import InquiryService  # noqa: E402
from app.services.quote_service import WhatsAppQuoteRenderer  # noqa: E402


class ConsoleMessenger:
    def send_text(self, to: str, body: str) -> None:
        print(f"\n--- message to {to} ---\n{body}\n---\n")


def build_message(arg: str, text_mode: bool) -> IncomingMessage:
    if text_mode:
        return IncomingMessage("local", "Tester", MediaKind.TEXT, text=arg)
    path = Path(arg)
    mime = mimetypes.guess_type(path.name)[0] or ""
    kind = (
        MediaKind.IMAGE if mime.startswith("image/")
        else MediaKind.PDF if mime == "application/pdf"
        else MediaKind.DOCX if path.suffix.lower() in (".docx", ".doc")
        else MediaKind.UNSUPPORTED
    )
    return IncomingMessage(
        "local", "Tester", kind, media_bytes=path.read_bytes(), media_mime=mime
    )


def main() -> None:
    text_mode = "--text" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--text"]
    if not args:
        print(__doc__)
        sys.exit(1)

    settings = load_settings()
    inventory = CsvInventoryRepository(settings.inventory_csv)
    service = InquiryService(
        extractor=ClaudeExtractor(settings.anthropic_api_key, settings.extraction_model),
        matcher=FuzzyBookMatcher(
            inventory,
            candidate_floor=settings.candidate_floor,
            confident_threshold=settings.auto_send_threshold,
        ),
        store=SqliteInquiryStore(":memory:"),
        messenger=ConsoleMessenger(),
        renderer=WhatsAppQuoteRenderer("our bookstore"),
        auto_send_threshold=settings.auto_send_threshold,
    )
    inquiry = service.handle_message(build_message(" ".join(args), text_mode))
    print(f"status: {inquiry.status.value}")
    for m in inquiry.matches:
        best = f"{m.best.item.title} ({m.best.score}%)" if m.best else "NO MATCH"
        print(f"  '{m.requested.title}' -> {best}")


if __name__ == "__main__":
    main()
