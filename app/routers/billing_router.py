import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from app.auth.sessions import get_verified_user
from app.database.database import get_db
from app.services import billing


async def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


billing_router = APIRouter(prefix="/billing", tags=["billing"], dependencies=[Depends(no_cache)])


class BillingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: Literal["en", "zh"] = "en"


@billing_router.get("/status")
async def status(request: Request, user=Depends(get_verified_user), db=Depends(get_db)):
    return await billing.status(db, user, request.app.state.stripe, request.app.state.config)


@billing_router.post("/checkout")
async def checkout(payload: BillingRequest, request: Request, user=Depends(get_verified_user), db=Depends(get_db)):
    return await billing.checkout(db, user, payload.locale, request.app.state.stripe, request.app.state.config)


@billing_router.post("/portal")
async def portal(payload: BillingRequest, request: Request, user=Depends(get_verified_user), db=Depends(get_db)):
    return await billing.portal(db, user, payload.locale, request.app.state.stripe, request.app.state.config)


@billing_router.post("/webhook")
async def webhook(request: Request, db=Depends(get_db)):
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    raise HTTPException(413)
    except TimeoutError:
        raise HTTPException(408) from None
    event = request.app.state.stripe.verify_event(bytes(body), request.headers.get("stripe-signature", ""))
    return await billing.webhook(db, event, request.app.state.stripe, request.app.state.config)
