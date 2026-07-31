"""Human review dashboard.

One page listing every inquiry that needs review; each ambiguous line
shows a dropdown of candidates. Approving posts corrections and sends
the quote. Protected by a bearer token (?token=... also accepted so it
works from a phone browser).
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.quote_service import jmd


def build_router(service, store, settings) -> APIRouter:
    router = APIRouter()

    def auth(request: Request) -> None:
        token = request.query_params.get("token") or (
            request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        )
        if token != settings.admin_token:
            raise HTTPException(status_code=401, detail="Bad or missing token")

    @router.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, _=Depends(auth)):
        pending = store.pending_review()
        stats = store.stats()
        token = settings.admin_token
        cards = []
        for inq in pending:
            rows = []
            for idx, m in enumerate(inq.matches):
                options = []
                cands = ([m.best] if m.best else []) + m.alternatives
                for c in cands:
                    stock_note = "" if c.item.in_stock else ", OUT OF STOCK"
                    label = (
                        f"{c.item.title} (JMD) {jmd(c.item.price)} "
                        f"({c.score:.0f}%{stock_note})"
                    )
                    sel = " selected" if (m.best and c.item.sku == m.best.item.sku) else ""
                    options.append(
                        f'<option value="{html.escape(c.item.sku)}"{sel}>'
                        f"{html.escape(label)}</option>"
                    )
                not_avail_sel = " selected" if not m.best else ""
                options.append(f'<option value=""{not_avail_sel}>— Not available / skip —</option>')
                score = f"{m.best.score:.0f}%" if m.best else "no match"
                rows.append(
                    f"<tr><td>{html.escape(m.requested.title)}"
                    f"<br><small>qty {m.requested.quantity} · best {score}</small></td>"
                    f'<td><select name="line-{idx}">{"".join(options)}</select></td></tr>'
                )
            cards.append(
                f"<form method='post' action='/review/{inq.id}/approve?token={token}'>"
                f"<h3>{html.escape(inq.sender_name or inq.sender)} "
                f"<small>({html.escape(inq.sender)})</small></h3>"
                f"<table>{''.join(rows)}</table>"
                f"<button type='submit'>Approve &amp; send quote</button></form><hr>"
            )
        body = f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Quote review</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}}
 table{{width:100%;border-collapse:collapse}} td{{padding:.4rem;border-bottom:1px solid #eee}}
 select{{max-width:100%;width:100%}} button{{margin:.6rem 0;padding:.5rem 1rem}}
 .stats{{color:#666}}
</style></head><body>
<h1>Pending review ({len(pending)})</h1>
<p class='stats'>Totals: {html.escape(str(stats))}</p>
{''.join(cards) if cards else '<p>Nothing waiting. ✨</p>'}
</body></html>"""
        return HTMLResponse(body)

    @router.post("/review/{inquiry_id}/approve")
    async def approve(inquiry_id: str, request: Request, _=Depends(auth)):
        form = await request.form()
        corrections: dict[int, str | None] = {}
        for key, value in form.items():
            if key.startswith("line-"):
                idx = int(key.removeprefix("line-"))
                corrections[idx] = value or None
        try:
            service.approve(inquiry_id, corrections)
        except KeyError:
            raise HTTPException(status_code=404, detail="Inquiry not found")
        return RedirectResponse(
            url=f"/review?token={settings.admin_token}", status_code=303
        )

    return router