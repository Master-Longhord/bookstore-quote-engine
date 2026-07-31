# Bookstore Quote Bot

WhatsApp customers send a school book list (photo, PDF, Word doc, or plain text). The bot reads it with the Claude API, matches every title against the shop's CSV inventory, and replies with a priced quote — automatically when every match is confident, or after a one-click human review when it isn't.

## How a message flows

```
WhatsApp → POST /webhook → queue → worker thread
                                     │
                          ClaudeExtractor (vision/PDF → JSON book list)
                                     │
                          FuzzyBookMatcher (vs inventory.csv)
                                     │
                    all lines ≥ threshold?  ──yes──► quote sent instantly
                                     │no
                          review dashboard (/review)
                                     │ shop owner picks correct matches
                                     ▼
                              quote sent manually-approved
```

## Architecture (SOLID)

- `app/domain/` — models + ports (interfaces). No framework imports.
- `app/adapters/` — one concrete implementation per external thing: Claude, WhatsApp Cloud API, CSV inventory, SQLite store, fuzzy matcher. Each is swappable behind its port (**Open/Closed**, **Liskov**).
- `app/services/` — `InquiryService` orchestrates the pipeline; `QuoteBuilder`/renderer do one job each (**Single Responsibility**). Services depend only on ports (**Dependency Inversion**); ports are small (**Interface Segregation**).
- `app/main.py` — the only place concrete classes are wired together.

## Setup

### 1. Inventory CSV

`data/inventory.csv` with columns: `sku,title,author_or_publisher,price,stock`. See the sample file. To update stock, just overwrite the file — it hot-reloads.

### 2. WhatsApp Cloud API (free at this volume)

1. Create a Meta developer app at developers.facebook.com → add the **WhatsApp** product.
2. Migrate/register the shop's business number there (note: a number can't be on the WhatsApp Business *app* and the Cloud API at the same time — migrating moves it).
3. Copy the **permanent access token** and **phone number ID** into `.env`.
4. After deploying, set the webhook URL to `https://your-app.up.railway.app/webhook`, subscribe to the `messages` field, and use your `WA_VERIFY_TOKEN` value in the verify field.

### 3. Anthropic API key

console.anthropic.com → API keys. Extraction uses Claude Haiku by default (`EXTRACTION_MODEL`), which is the cheapest option and comfortably reads photographed book lists.

## Deploy on Railway

```bash
git init && git add . && git commit -m "init"
# push to GitHub, then in Railway: New Project → Deploy from repo
```

Set the variables from `.env.example` in the Railway dashboard. Add a **volume** mounted at `/srv/data` so the SQLite DB and inventory survive restarts. Done — Railway builds the Dockerfile and gives you the public URL for the webhook.

## Deploy on a $5 VPS

```bash
sudo apt install python3-venv && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill it in
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Put Caddy or nginx in front for HTTPS (Meta requires HTTPS webhooks). A systemd unit keeps it running.

## Day-to-day operation

- **Review queue:** open `https://your-app/review?token=YOUR_ADMIN_TOKEN` on any phone. Each pending inquiry shows dropdowns of candidate matches; pick, approve, quote goes out.
- **Health/stats:** `GET /health` shows inventory size and inquiry counts by status.
- **Tune automation:** raise `AUTO_SEND_THRESHOLD` (e.g. 92) for fewer wrong auto-quotes, lower it (e.g. 82) for fewer reviews.

## Draining the 13k backlog

Old chats in the WhatsApp Business app can't be read programmatically. Instead:

1. Export/collect contact numbers into `contacts.csv` (column `phone`, digits only, country code included).
2. In Meta Business Manager, create a message **template** (e.g. `booklist_resend`) asking customers to resend their book list, and wait for approval — templates are required for business-initiated messages outside the 24-hour window.
3. Run:

```bash
python scripts/broadcast.py contacts.csv booklist_resend
```

It rate-limits itself and logs progress to `sent_log.txt`, so it's safe to stop and resume. Every resent list then flows through the automated pipeline.

## Test locally

```bash
python -m unittest discover tests -v          # offline, no keys needed
python scripts/quote_local.py --text "New Gen Maths SS1, 2x Verbal Reasoning Pry 4"
python scripts/quote_local.py samples/booklist.jpg   # needs ANTHROPIC_API_KEY
```

## Costs (rough)

- Railway hobby ~$5/mo (or any VPS)
- Claude Haiku extraction: fractions of a cent per book list
- WhatsApp: inbound + replies within 24h are free; template broadcasts are billed per message by Meta (check current rates for Nigeria in Business Manager before blasting 13k)


docker run -d --name bookstore-app --env-file .env -p 8000:8000 bookstore-bot
docker build --no-cache -t bookstore-bot .
docker stop bookstore-app
docker logs -f bookstore-app
docker rm bookstore-app
