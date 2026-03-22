"""Web Push notifications using VAPID."""

import json

from fastapi import APIRouter, Depends
from pywebpush import WebPushException, webpush
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from convocation.auth.deps import get_current_user
from convocation.auth.models import PushSubscription, User
from convocation.config import settings
from convocation.db import get_db

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict  # {p256dh: str, auth: str}


@router.post("/subscribe")
async def subscribe(
    req: SubscribeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Register a push subscription for the current user."""
    # Upsert — update if endpoint exists
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == req.endpoint)
    )
    sub = result.scalar_one_or_none()

    if sub:
        sub.p256dh = req.keys["p256dh"]
        sub.auth_key = req.keys["auth"]
        sub.user_id = user.id
    else:
        sub = PushSubscription(
            user_id=user.id,
            endpoint=req.endpoint,
            p256dh=req.keys["p256dh"],
            auth_key=req.keys["auth"],
        )
        db.add(sub)

    await db.commit()
    return {"ok": True}


@router.delete("/unsubscribe")
async def unsubscribe(
    req: SubscribeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a push subscription."""
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == req.endpoint)
    )
    sub = result.scalar_one_or_none()
    if sub:
        await db.delete(sub)
        await db.commit()
    return {"ok": True}


@router.get("/vapid-key")
async def get_vapid_key():
    """Return the public VAPID key for client-side subscription."""
    return {"publicKey": settings.vapid_public_key}


async def send_push_notification(
    title: str,
    body: str,
    db_session: AsyncSession | None = None,
    url: str = "/",
):
    """Send push notification to all subscribers."""
    if not settings.vapid_private_key:
        return

    from convocation.db import async_session

    if db_session:
        session = db_session
    else:
        session = async_session()

    try:
        result = await session.execute(select(PushSubscription))
        subs = result.scalars().all()

        payload = json.dumps({"title": title, "body": body, "url": url})

        for sub in subs:
            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth_key},
            }
            try:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": f"mailto:{settings.vapid_claims_email}"},
                )
            except WebPushException:
                # Subscription might be expired — remove it
                await session.delete(sub)

        await session.commit()
    finally:
        if not db_session:
            await session.close()
