from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services.quote_service import jmd

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

    @router.post("/review/{inquiry_id}/approve")
    async def approve(inquiry_id: str, request: Request, _=Depends(auth)):
        form = await request.form()
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