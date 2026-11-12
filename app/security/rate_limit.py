from fastapi import HTTPException
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert

from app.auth.audit import client_ip, keyed_hash
from app.models.auth import AuthRateLimit
from app.utils.time import utc_now_seconds


async def enforce_rate_limit(db, request, purpose: str, identity: str = "", *, limit=10, window=900):
    """Atomic shared counters, committed separately from a rejected auth transaction."""
    now = utc_now_seconds()
    for dimension, value, budget in (("ip", client_ip(request), 30), ("identity", identity, limit)):
        if dimension == "identity" and not identity:
            continue
        key = keyed_hash(request.app.state.config, f"rate:{purpose}:{dimension}", value)
        statement = insert(AuthRateLimit).values(key_hash=key, window_at=now, count=1, expires_at=now + window)
        expired = AuthRateLimit.window_at <= now - window
        statement = statement.on_conflict_do_update(index_elements=[AuthRateLimit.key_hash], set_={
            "window_at": case((expired, now), else_=AuthRateLimit.window_at),
            "count": case((expired, 1), else_=AuthRateLimit.count + 1),
            "expires_at": case((expired, now + window), else_=AuthRateLimit.expires_at),
        }).returning(AuthRateLimit.count, AuthRateLimit.expires_at)
        count, expires = (await db.execute(statement)).one()
        await db.commit()
        if count > budget:
            raise HTTPException(429, detail={"code": "auth_rate_limited", "message": "Too many attempts. Try again later."},
                                headers={"Retry-After": str(max(1, expires - now))})
