from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import time
from pydantic import BaseModel
from app.services.quote_service import jmd
from app.domain.models import InquiryStatus

class ClaimRequest(BaseModel):
    token: str

# Point FastAPI to the templates folder
templates = Jinja2Templates(directory="app/templates")

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
        pending_payments = store.pending_payments()
        stats = store.stats()
        
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "pending": pending,
                "pending_payments": pending_payments,
                "stats": stats,
                "token": settings.admin_token,
                "jmd": jmd
            }
        )

    # ---- Concurrency Polling Endpoint ----
    @router.get("/review/api/pending")
    def get_pending_reviews(_=Depends(auth)):
        """Lightweight endpoint for frontend JS to poll the current claim status."""
        inquiries = store.pending_review()
        return {
            "server_time": time.time(),
            "locks": [
                {
                    "id": inq.id,
                    "claimed_by": inq.claimed_by,
                    "claimed_at": inq.claimed_at,
                }
                for inq in inquiries
            ]
        }

    # ---- Lock Management Endpoints ----
    @router.post("/review/{inquiry_id}/claim")
    def claim_inquiry(inquiry_id: str, payload: ClaimRequest, _=Depends(auth)):
        inquiry = store.get(inquiry_id)
        if not inquiry:
            raise HTTPException(status_code=404, detail="Inquiry not found")
        
        now = time.time()
        
        if inquiry.claimed_by and inquiry.claimed_by != payload.token:
            if inquiry.claimed_at and (now - inquiry.claimed_at) < 300:
                raise HTTPException(status_code=409, detail="Currently locked by another user")
                
        inquiry.claimed_by = payload.token
        inquiry.claimed_at = now
        store.save(inquiry)
        return {"status": "claimed"}

    @router.post("/review/{inquiry_id}/unclaim")
    def unclaim_inquiry(inquiry_id: str, payload: ClaimRequest, _=Depends(auth)):
        inquiry = store.get(inquiry_id)
        if not inquiry:
            raise HTTPException(status_code=404)
            
        if inquiry.claimed_by == payload.token:
            inquiry.claimed_by = None
            inquiry.claimed_at = None
            store.save(inquiry)
            
        return {"status": "unclaimed"}

    # ---- Existing Approval Endpoint ----
    @router.post("/review/{inquiry_id}/approve")
    async def approve(inquiry_id: str, request: Request, _=Depends(auth)):
        form = await request.form()
        
        inquiry = store.get(inquiry_id)
        if not inquiry:
            raise HTTPException(status_code=404, detail="Inquiry not found")
            
        client_token = form.get("reviewer_token")
        now = time.time()
        
        if inquiry.claimed_by and inquiry.claimed_by != client_token:
            if inquiry.claimed_at and (now - inquiry.claimed_at) < 300:
                raise HTTPException(status_code=403, detail="Cannot approve. Locked by another user.")
        
        corrections: dict[int, str | None] = {}
        
        for key, value in form.items():
            if key.startswith("line-"):
                idx = int(key.removeprefix("line-"))
                corrections[idx] = value or None
                
            elif key.startswith("manual_title-"):
                idx = int(key.removeprefix("manual_title-"))
                title = value.strip()
                
                if title:
                    price_str = form.get(f"manual_price-{idx}", "0").strip()
                    price = price_str if price_str else "0"
                    corrections[idx] = f"CUSTOM::{title}::{price}"

        try:
            service.approve(inquiry_id, corrections)
        except KeyError:
            raise HTTPException(status_code=404, detail="Inquiry not found")
            
        return RedirectResponse(
            url=f"/review?token={settings.admin_token}", status_code=303
        )

    # ---- NEW: Payment Confirmation Endpoint ----
    @router.post("/review/{inquiry_id}/confirm_payment")
    def confirm_payment(inquiry_id: str, _=Depends(auth)):
        """Verifies payment, updates state, and sends the final PDF receipt."""
        inquiry = store.get(inquiry_id)
        if not inquiry:
            raise HTTPException(status_code=404, detail="Inquiry not found")
            
        inquiry.status = InquiryStatus.CONFIRMED
        store.save(inquiry)

        try:
            active_quote = inquiry.revised_quote or inquiry.quote
            pdf_bytes = service._pdf_generator.generate_quote_pdf(inquiry, active_quote)
            
            service._messenger.send_text(
                inquiry.sender,
                "Payment received! Thank you. Your final receipt is attached below. Your order is now being processed for delivery."
            )
            
            service._messenger.send_document(
                to=inquiry.sender,
                document_bytes=pdf_bytes,
                filename=f"Receipt_{inquiry.id[:8]}.pdf"
            )
        except Exception as exc:
            # If the PDF generation fails, we still want the order to be confirmed in the DB
            print(f"Failed to generate/send PDF for {inquiry.id}: {exc}")
            service._messenger.send_text(
                inquiry.sender,
                "Payment received! We had a slight error generating your PDF receipt, but your order is fully confirmed and processing."
            )
            
        return RedirectResponse(
            url=f"/review?token={settings.admin_token}", status_code=303
        )

    return router