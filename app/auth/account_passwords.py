from fastapi import HTTPException

from app.auth.audit import record_auth_event
from app.auth.email_flows import (
    auth_error,
    check_password,
    invalidate_recovery,
    locked_user,
    require_password_auth,
)
from app.auth.notifications import notify
from app.auth.sessions import create_auth_session, revoke_user_sessions
from app.auth.user_profiles import account_security_summary, serialize_user_profile
from app.email.templates import AuthEmailTemplate
from app.security.rate_limit import enforce_rate_limit
from app.utils.time import utc_now_seconds


async def update_password(db, request, response, current_user, password, *, current_password=None):
    require_password_auth(request)
    user_id, previous = current_user.id, current_user.password_hash
    await enforce_rate_limit(db, request, "account_password", user_id, limit=5)
    if not current_user.email_verified_at:
        raise auth_error(409, "email_verification_required", "Verify your email first.")
    if current_password is not None:
        if previous is None:
            raise auth_error(409, "password_not_set", "Set a password first.")
        if not await request.app.state.passwords.verify(current_password, previous):
            raise auth_error(400, "invalid_current_password", "Current password is incorrect.")
        if current_password == password:
            raise auth_error(400, "password_reuse", "Choose a different password.")
    else:
        if previous:
            raise auth_error(409, "password_already_set", "Password is already set.")
        if not current_user.email_verified_at:
            raise auth_error(409, "email_verification_required", "Verify your email first.")
        if request.state.auth_session.created_at < utc_now_seconds() - 300:
            raise auth_error(403, "reauthentication_required", "Sign in again before setting a password.")
    check_password(password, current_user.email_normalized, current_user.name)
    await db.rollback()
    encoded = await request.app.state.passwords.hash(password)
    user = await locked_user(db, user_id)
    if user.password_hash != previous:
        raise HTTPException(409, detail={"code": "credentials_changed", "message": "Sign in again."})
    user.password_hash, user.password_updated_at = encoded, utc_now_seconds()
    await revoke_user_sessions(db, user.id)
    await invalidate_recovery(db, user.id)
    await create_auth_session(db, user, request, response)
    notify(db, request, user, AuthEmailTemplate.PASSWORD_CHANGED)
    record_auth_event(db, request, "password_changed" if previous else "password_set", user)
    await db.commit()
    return {"ok": True, "message": "Password updated." if previous else "Password set.",
            "user": serialize_user_profile(user, request.app.state.config), "security": await account_security_summary(db, user)}
