"""Drain the 13k backlog: ask everyone to resend their book list.

The old WhatsApp Business *app* chats can't be read programmatically, so
instead we message every known contact; whoever resends flows through
the automated pipeline and gets an instant quote.

Prerequisites:
1. Export contact numbers to a CSV with a 'phone' column (international
   format, no '+', e.g. 2348012345678). The Business app can export
   contacts; or copy from her phone contacts.
2. Create and get approval for a template in Meta Business Manager,
   e.g. name 'booklist_resend', body:
   "Hello! Thanks for contacting us about your school book list. To get
    your instant quote, simply resend the book list (photo, PDF or text)
    right here. We reply in under a minute!"

Usage:
    python scripts/broadcast.py contacts.csv booklist_resend

Sends at a gentle rate and records progress in sent_log.txt so it can be
stopped and resumed without double-messaging anyone.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.whatsapp_client import WhatsAppClient  # noqa: E402
from app.config import load_settings  # noqa: E402

RATE_PER_SECOND = 5          # stay well under Meta's pair-rate limits
LOG = Path("sent_log.txt")


def main(contacts_csv: str, template_name: str) -> None:
    settings = load_settings()
    client = WhatsAppClient(settings.wa_access_token, settings.wa_phone_number_id)

    already = set(LOG.read_text().split()) if LOG.exists() else set()
    with open(contacts_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    numbers = []
    seen = set()
    for row in rows:
        phone = "".join(ch for ch in (row.get("phone") or "") if ch.isdigit())
        if phone and phone not in seen:
            seen.add(phone)
            numbers.append(phone)

    todo = [n for n in numbers if n not in already]
    print(f"{len(numbers)} unique contacts, {len(todo)} still to send.")

    ok = failed = 0
    with LOG.open("a") as logf:
        for i, phone in enumerate(todo, 1):
            try:
                client.send_template(phone, template_name)
                logf.write(phone + "\n")
                logf.flush()
                ok += 1
            except Exception as exc:  # keep going; log and move on
                failed += 1
                print(f"  ! {phone}: {exc}")
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} sent (ok={ok} failed={failed})")
            time.sleep(1 / RATE_PER_SECOND)

    print(f"Done. ok={ok} failed={failed}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
