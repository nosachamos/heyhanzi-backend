"""Email credentials, verification and recovery with serialized account mutations."""
from fastapi import HTTPException
from sqlalchemy import delete, select, text, update

from app.auth.audit import record_auth_event
from app.auth.config import flag
from app.auth.notifications import notify, remember_trusted_device_login
from app.auth.sessions import create_auth_session, revoke_user_sessions
from app.auth.tokens import create_opaque_token, hash_opaque_token
from app.auth.user_profiles import serialize_user_profile
from app.email.config import build_app_url
from app.email.templates import AuthEmailTemplate
from app.models.auth import AuthToken, TrustedDevice, User
from app.security.passwords import needs_rehash, validate_password
from app.security.rate_limit import enforce_rate_limit
from app.services.learning import ensure_enrollment, get_track
from app.utils.time import utc_now_seconds

SIGNUP_RESPONSE = {"ok": True, "message": "Check your email to verify your account."}
RESET_RESPONSE = {"ok": True, "message": "If an account exists for that email, we will send password reset instructions."}


def auth_error(status, code, message, **extra):
    return HTTPException(status, detail={"code": code, "message": message, **extra})


def require_password_auth(request, *, signup=False):
    config = request.app.state.config
    if not flag(config, "EmailPasswordEnabled") or (signup and not flag(config, "EmailSignupEnabled")):
        raise HTTPException(404, detail="Email authentication is unavailable")


def check_password(password, email, name=None):
    result = validate_password(password, email=email, display_name=name)
    if not result.valid:
        raise auth_error(400, "invalid_password", "Choose a stronger password.", issues=[i.code for i in result.issues])


async def lock_email(db, email):
    # Locks also cover absent users, preventing duplicate signup/provider races.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:email, 91731))"), {"email": email})


async def locked_user(db, user_id):
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if user is None or user.disabled_at:
        raise HTTPException(401, detail="Not authenticated")
    return user


async def issue_email_token(db, request, user, purpose):
    now = utc_now_seconds()
    await db.execute(update(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == purpose,
                                            AuthToken.consumed_at.is_(None)).values(consumed_at=now))
    token = create_opaque_token()
    expires = now + (86400 if purpose == "verify" else 1800)
    db.add(AuthToken(token_hash=hash_opaque_token(token), user_id=user.id,
                     email_normalized=user.email_normalized, purpose=purpose, expires_at=expires))
    path = "/verify-email" if purpose == "verify" else "/reset-password"
    template = AuthEmailTemplate.WELCOME_VERIFY if purpose == "verify" else AuthEmailTemplate.PASSWORD_RESET
    # Fragment tokens stay out of frontend access logs and HTTP Referer headers.
    action = build_app_url(path, config=request.app.state.config) + "#token=" + token
    notify(db, request, user, template, action_url=action, expires_at=expires)


async def signup_with_email_password(db, request, response, payload):
    require_password_auth(request, signup=True)
    await enforce_rate_limit(db, request, "signup", payload.email, limit=3)
    await request.app.state.turnstile.verify(payload.turnstileToken, "signup")
    if payload.learningTrackId:
        await get_track(db, payload.learningTrackId)
    check_password(payload.password, payload.email)
    encoded = await request.app.state.passwords.hash(payload.password)
    await lock_email(db, payload.email)
    user = await db.scalar(select(User).where(User.email_normalized == payload.email).with_for_update())
    if user:
        # Never change a pre-existing password, identity, or session on duplicate signup.
        await db.rollback()
        return SIGNUP_RESPONSE
    user = User(name=payload.email.split("@")[0][:64], email_normalized=payload.email,
                password_hash=encoded, password_updated_at=utc_now_seconds())
    db.add(user)
    await db.flush()
    if payload.learningTrackId:
        await ensure_enrollment(db, user.id, payload.learningTrackId)
    await issue_email_token(db, request, user, "verify")
    await create_auth_session(db, user, request, response)
    record_auth_event(db, request, "signup_created", user)
    await db.commit()
    return {**SIGNUP_RESPONSE, "user": serialize_user_profile(user, request.app.state.config)}


async def login_with_email_password(db, request, response, payload):
    require_password_auth(request)
    await enforce_rate_limit(db, request, "login", payload.email, limit=10)
    await request.app.state.turnstile.verify(payload.turnstileToken, "login")
    user = await db.scalar(select(User).where(User.email_normalized == payload.email))
    user_id, original = (user.id, user.password_hash) if user else (None, None)
    # Release the connection while the CPU/memory-bound password check runs.
    await db.rollback()
    valid = await request.app.state.passwords.verify(payload.password, original)
    upgraded = await request.app.state.passwords.hash(payload.password) if valid and original and needs_rehash(original, config={}) else None
    if user_id:
        user = await locked_user(db, user_id)
        valid = valid and original is not None and user.password_hash == original
    else:
        user = None
        valid = False
    if not valid:
        if user:
            now = utc_now_seconds()
            if user.failure_window_at < now - 900:
                user.failure_window_at, user.failed_logins = now, 0
            user.failed_logins += 1
            if user.email_verified_at and user.failed_logins >= 5 and user.failure_alert_at < now - 900:
                notify(db, request, user, AuthEmailTemplate.FAILED_LOGIN_ALERT,
                       action_url=build_app_url("/forgot-password", config=request.app.state.config),
                       failure_count=user.failed_logins)
                user.failure_alert_at = now
        record_auth_event(db, request, "login_failure", user)
        await db.commit()
        raise auth_error(401, "invalid_credentials", "Invalid email or password.")
    user.failed_logins = 0
    if upgraded:
        user.password_hash = upgraded
    await create_auth_session(db, user, request, response)
    await remember_trusted_device_login(db, request, response, user)
    record_auth_event(db, request, "login_success", user)
    await db.commit()
    return serialize_user_profile(user, request.app.state.config)


async def active_token(db, raw, purpose):
    if not raw or len(raw) > 128:
        raise auth_error(400, "invalid_or_expired_token", "This link is invalid or expired.")
    token = await db.get(AuthToken, hash_opaque_token(raw))
    if not token or token.purpose != purpose or token.consumed_at or token.expires_at <= utc_now_seconds():
        raise auth_error(400, "invalid_or_expired_token", "This link is invalid or expired.")
    return token


async def consume_token(db, raw, purpose):
    preliminary = await active_token(db, raw, purpose)
    user = await locked_user(db, preliminary.user_id)
    token = await db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_opaque_token(raw))
                            .with_for_update().execution_options(populate_existing=True))
    if token.consumed_at or token.expires_at <= utc_now_seconds() or token.email_normalized != user.email_normalized:
        raise auth_error(400, "invalid_or_expired_token", "This link is invalid or expired.")
    token.consumed_at = utc_now_seconds()
    return user


async def verify_email_token(db, request, current_user, token):
    await enforce_rate_limit(db, request, "verify", current_user.id)
    checked = await active_token(db, token, "verify")
    if checked.user_id != current_user.id:
        raise auth_error(400, "invalid_or_expired_token", "This link is invalid or expired.")
    user = await consume_token(db, token, "verify")
    user.email_verified_at = utc_now_seconds()
    user.email_verification_source = "email"
    await revoke_user_sessions(db, user.id, except_id=request.state.auth_session.id)
    record_auth_event(db, request, "email_verified", user)
    await db.commit()
    return {"ok": True, "message": "Email verified.", "user": serialize_user_profile(user, request.app.state.config)}


async def resend_verification(db, request, current_user, turnstile_token=None):
    await enforce_rate_limit(db, request, "resend", current_user.id, limit=3)
    await request.app.state.turnstile.verify(turnstile_token, "resend_verification")
    user = await locked_user(db, current_user.id)
    if user.email_verified_at:
        return {"ok": True, "message": "Email already verified."}
    await issue_email_token(db, request, user, "verify")
    record_auth_event(db, request, "email_verification_sent", user)
    await db.commit()
    return {"ok": True, "message": "Verification email sent."}


async def request_password_reset(db, request, email, turnstile_token=None):
    require_password_auth(request)
    await enforce_rate_limit(db, request, "forgot", email, limit=3)
    await request.app.state.turnstile.verify(turnstile_token, "forgot_password")
    user = await db.scalar(select(User).where(User.email_normalized == email).with_for_update())
    if user and user.password_hash and not user.disabled_at:
        await issue_email_token(db, request, user, "reset")
        record_auth_event(db, request, "password_reset_requested", user)
        await db.commit()
    return RESET_RESPONSE


async def invalidate_recovery(db, user_id):
    await db.execute(update(AuthToken).where(AuthToken.user_id == user_id,
                                            AuthToken.consumed_at.is_(None)).values(consumed_at=utc_now_seconds()))
    await db.execute(delete(TrustedDevice).where(TrustedDevice.user_id == user_id))


async def complete_password_reset(db, request, payload):
    require_password_auth(request)
    await enforce_rate_limit(db, request, "reset")
    token = await active_token(db, payload.token, "reset")
    user = await db.get(User, token.user_id)
    check_password(payload.password, user.email_normalized, user.name)
    await db.rollback()
    encoded = await request.app.state.passwords.hash(payload.password)
    user = await consume_token(db, payload.token, "reset")
    user.password_hash, user.password_updated_at = encoded, utc_now_seconds()
    if not user.email_verified_at:
        user.email_verified_at, user.email_verification_source = utc_now_seconds(), "password_reset"
    await revoke_user_sessions(db, user.id)
    await invalidate_recovery(db, user.id)
    notify(db, request, user, AuthEmailTemplate.PASSWORD_CHANGED)
    record_auth_event(db, request, "password_reset_completed", user)
    await db.commit()
    return {"ok": True, "message": "Password reset complete."}
