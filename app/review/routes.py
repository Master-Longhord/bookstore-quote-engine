from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import time
from pydantic import BaseModel
from app.services.quote_service import jmd

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
        stats = store.stats()
        
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "pending": pending,
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
        
        # Reject if claimed by someone else and the 5-minute (300s) TTL is still active
        if inquiry.claimed_by and inquiry.claimed_by != payload.token:
            if inquiry.claimed_at and (now - inquiry.claimed_at) < 300:
                raise HTTPException(status_code=409, detail="Currently locked by another user")
                
        # Grant or refresh the claim
        inquiry.claimed_by = payload.token
        inquiry.claimed_at = now
        store.save(inquiry)
        return {"status": "claimed"}

    @router.post("/review/{inquiry_id}/unclaim")
    def unclaim_inquiry(inquiry_id: str, payload: ClaimRequest, _=Depends(auth)):
        inquiry = store.get(inquiry_id)
        if not inquiry:
            raise HTTPException(status_code=404)
            
        # Only the person who owns the lock can release it early
        if inquiry.claimed_by == payload.token:
            inquiry.claimed_by = None
            inquiry.claimed_at = None
            store.save(inquiry)
            
        return {"status": "unclaimed"}

    # ---- Existing Approval Endpoint (Updated with Lock Validation) ----
    @router.post("/review/{inquiry_id}/approve")
    async def approve(inquiry_id: str, request: Request, _=Depends(auth)):
        form = await request.form()
        
        # 1. Fetch inquiry and validate lock before doing any processing
        inquiry = store.get(inquiry_id)
        if not inquiry:
            raise HTTPException(status_code=404, detail="Inquiry not found")
            
        client_token = form.get("reviewer_token")
        now = time.time()
        
        # If someone else owns the lock and it hasn't expired, reject the submission
        if inquiry.claimed_by and inquiry.claimed_by != client_token:
            if inquiry.claimed_at and (now - inquiry.claimed_at) < 300:
                raise HTTPException(status_code=403, detail="Cannot approve. Locked by another user.")
        
        # 2. Process form data
        corrections: dict[int, str | None] = {}
        
        for key, value in form.items():
            # Handle standard SKU selection from the dropdown
            if key.startswith("line-"):
                idx = int(key.removeprefix("line-"))
                corrections[idx] = value or None
                
            # Handle manual text and price inputs
            elif key.startswith("manual_title-"):
                idx = int(key.removeprefix("manual_title-"))
                title = value.strip()
                
                # Only process if they actually typed a title
                if title:
                    # Fetch the corresponding price, defaulting to "0" if empty
                    price_str = form.get(f"manual_price-{idx}", "0").strip()
                    price = price_str if price_str else "0"
                    
                    # Encode the data into our strict contract format
                    corrections[idx] = f"CUSTOM::{title}::{price}"

        try:
            service.approve(inquiry_id, corrections)
        except KeyError:
            raise HTTPException(status_code=404, detail="Inquiry not found")
            
        return RedirectResponse(
            url=f"/review?token={settings.admin_token}", status_code=303
        )
    
    return router