import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, update

from app.auth.tokens import create_opaque_token, hash_opaque_token
from app.database.database import get_db
from app.environment import allowed_origins
from app.models.auth import AuthSession, User
from app.utils.time import utc_now_seconds

AUTH_SESSION_COOKIE_NAME = "__Host-heyhanzi_session"
CSRF_COOKIE_NAME = "heyhanzi_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
DEVICE_COOKIE_NAME = "__Host-heyhanzi_device"


def require_origin(request: Request):
    if request.headers.get("origin") not in allowed_origins(request.app.state.config):
        raise HTTPException(403, detail={"code": "invalid_origin", "message": "Request origin is not allowed."})


def csrf_token(config: dict, token: str) -> str:
    return hmac.new(config["AUTH"]["SecretKey"].encode(), f"csrf:{token}".encode(), hashlib.sha256).hexdigest()


def set_auth_cookies(response: Response, token: str, config: dict):
    lifetime = 30 * 24 * 60 * 60
    csrf = csrf_token(config, token)
    response.set_cookie(AUTH_SESSION_COOKIE_NAME, token, max_age=lifetime,
                        secure=True, httponly=True, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE_NAME, csrf, max_age=lifetime,
                        secure=True, httponly=False, samesite="lax", path="/")
    response.headers[CSRF_HEADER_NAME] = csrf


def clear_auth_cookies(response: Response):
    for name in (AUTH_SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(name, secure=True, httponly=name == AUTH_SESSION_COOKIE_NAME, samesite="lax", path="/")


async def create_auth_session(db, user, request, response):
    token = create_opaque_token()
    csrf = csrf_token(request.app.state.config, token)
    # Rotate any session presented at this browser's login boundary.
    previous = request.cookies.get(AUTH_SESSION_COOKIE_NAME)
    if previous:
        await db.execute(update(AuthSession).where(
            AuthSession.session_token_hash == hash_opaque_token(previous)
        ).values(revoked_at=utc_now_seconds()))
    db.add(AuthSession(user_id=user.id, session_token_hash=hash_opaque_token(token),
                       csrf_token_hash=hash_opaque_token(csrf), expires_at=utc_now_seconds() + 2592000))
    set_auth_cookies(response, token, request.app.state.config)


async def get_current_user(request: Request, response: Response, db=Depends(get_db)):
    token = request.cookies.get(AUTH_SESSION_COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(401, detail="Not authenticated")
    row = (await db.execute(select(AuthSession, User).join(User, User.id == AuthSession.user_id).where(
        AuthSession.session_token_hash == hash_opaque_token(token),
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utc_now_seconds(), User.disabled_at.is_(None),
    ))).first()
    if row is None:
        raise HTTPException(401, detail="Not authenticated")
    auth_session, user = row
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request)
        supplied = request.headers.get(CSRF_HEADER_NAME, "")
        if not supplied or not secrets.compare_digest(hash_opaque_token(supplied), auth_session.csrf_token_hash):
            raise HTTPException(403, detail={"code": "csrf_failed", "message": "CSRF validation failed."})
    response.headers[CSRF_HEADER_NAME] = csrf_token(request.app.state.config, token)
    request.state.auth_session = auth_session
    return user


async def get_verified_user(user=Depends(get_current_user)):
    """Use this dependency for learning data and other verified-account features."""
    if user.email_verified_at is None:
        raise HTTPException(403, detail={"code": "email_verification_required", "message": "Verify your email first."})
    return user


async def revoke_user_sessions(db, user_id: str, *, except_id: str | None = None):
    query = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_id:
        query = query.where(AuthSession.id != except_id)
    await db.execute(query.values(revoked_at=utc_now_seconds()))
