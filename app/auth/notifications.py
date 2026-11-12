from sqlalchemy import select

from app.auth.audit import record_auth_event
from app.auth.sessions import DEVICE_COOKIE_NAME
from app.auth.tokens import create_opaque_token, hash_opaque_token
from app.email.config import build_app_url
from app.email.outbox import enqueue_email
from app.email.templates import AuthEmailContext, AuthEmailTemplate, build_auth_email_message
from app.models.auth import TrustedDevice
from app.utils.time import utc_now_seconds


def notify(db, request, user, template, *, action_url=None, expires_at=None, failure_count=None):
    config = request.app.state.config
    # Store only a coarse device label; no raw user-agent or IP in emails.
    ua = request.headers.get("user-agent", "").lower()
    browser = next((label for marker, label in (("firefox", "Firefox"), ("edg/", "Edge"),
                    ("chrome", "Chrome"), ("safari", "Safari")) if marker in ua), "Web browser")
    message = build_auth_email_message(template, AuthEmailContext(
        to_email=user.email_normalized, user_name=user.name, action_url=action_url,
        occurred_at=utc_now_seconds(), user_agent_summary=browser, failure_count=failure_count,
        failure_window_minutes=15, oauth_only_provider="google" if not user.password_hash else None,
    ), config=config)
    enqueue_email(db, config, message, expires_at=expires_at)


async def remember_trusted_device_login(db, request, response, user):
    now = utc_now_seconds()
    token = request.cookies.get(DEVICE_COOKIE_NAME, "")
    known = await db.scalar(select(TrustedDevice).where(
        TrustedDevice.user_id == user.id, TrustedDevice.token_hash == hash_opaque_token(token),
        TrustedDevice.expires_at > now,
    )) if token and len(token) <= 128 else None
    if known:
        return
    token = create_opaque_token()
    db.add(TrustedDevice(user_id=user.id, token_hash=hash_opaque_token(token), expires_at=now + 15552000))
    response.set_cookie(DEVICE_COOKIE_NAME, token, secure=True, httponly=True,
                        samesite="lax", path="/", max_age=15552000)
    if user.email_verified_at and user.new_device_alert_at <= now - 900:
        notify(db, request, user, AuthEmailTemplate.NEW_DEVICE_LOGIN,
               action_url=build_app_url("/account/security", config=request.app.state.config))
        user.new_device_alert_at = now
        record_auth_event(db, request, "new_device_login", user)
