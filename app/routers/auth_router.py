import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import update

from app.auth.audit import record_auth_event
from app.auth.config import flag
from app.auth.email_flows import (
    active_token,
    complete_password_reset,
    login_with_email_password,
    request_password_reset,
    signup_with_email_password,
    verify_email_token,
)
from app.auth.oauth import OAUTH_COOKIE, PROVIDERS, begin_oauth, complete_oauth
from app.auth.sessions import clear_auth_cookies, get_current_user, require_origin
from app.auth.user_profiles import serialize_user_profile
from app.database.database import get_db
from app.models.auth import AuthSession
from app.schemas.auth import (
    EmailCredentials,
    EmailRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenRequest,
)
from app.security.rate_limit import enforce_rate_limit
from app.security.turnstile import public_policy
from app.utils.time import utc_now_seconds

auth_router = APIRouter(prefix="/auth", tags=["authentication"])


@auth_router.get("/security")
async def security(request: Request):
    return public_policy(request.app.state.config)


@auth_router.get("/providers")
async def providers(request: Request):
    config = request.app.state.config
    return {"providers": [name for name, spec in PROVIDERS.items()
                           if config.get("OAUTH", {}).get(spec["config"], {}).get("ClientId")],
            "emailPasswordEnabled": flag(config, "EmailPasswordEnabled"),
            "emailSignupEnabled": flag(config, "EmailPasswordEnabled") and flag(config, "EmailSignupEnabled")}


@auth_router.post("/email/signup", dependencies=[Depends(require_origin)])
async def signup_email(payload: SignupRequest, request: Request, response: Response, db=Depends(get_db)):
    return await signup_with_email_password(db, request, response, payload)


@auth_router.post("/email/login", dependencies=[Depends(require_origin)])
async def login_email(payload: EmailCredentials, request: Request, response: Response, db=Depends(get_db)):
    return await login_with_email_password(db, request, response, payload)


@auth_router.get("/email/verify")
async def validate_verification(request: Request, token: str = Query(max_length=128), db=Depends(get_db)):
    await enforce_rate_limit(db, request, "validate_token")
    await active_token(db, token, "verify")
    return {"ok": True, "message": "Verification token is valid. Sign in and confirm to verify."}


@auth_router.post("/email/verify")
async def verify_email(payload: TokenRequest, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    return await verify_email_token(db, request, user, payload.token)


@auth_router.post("/password/forgot", dependencies=[Depends(require_origin)])
async def forgot_password(payload: EmailRequest, request: Request, db=Depends(get_db)):
    return await request_password_reset(db, request, payload.email, payload.turnstileToken)


@auth_router.get("/password/reset/validate")
async def validate_reset(request: Request, token: str = Query(max_length=128), db=Depends(get_db)):
    await enforce_rate_limit(db, request, "validate_token")
    await active_token(db, token, "reset")
    return {"ok": True, "message": "Reset token is valid."}


@auth_router.post("/password/reset", dependencies=[Depends(require_origin)])
async def reset_password(payload: ResetPasswordRequest, request: Request, response: Response, db=Depends(get_db)):
    result = await complete_password_reset(db, request, payload)
    clear_auth_cookies(response)
    return result


@auth_router.get("/session")
async def get_auth_session(request: Request, user=Depends(get_current_user)):
    return serialize_user_profile(user, request.app.state.config)


@auth_router.post("/logout")
async def logout(request: Request, response: Response, user=Depends(get_current_user), db=Depends(get_db)):
    await db.execute(update(AuthSession).where(AuthSession.id == request.state.auth_session.id).values(revoked_at=utc_now_seconds()))
    record_auth_event(db, request, "logout", user)
    await db.commit()
    clear_auth_cookies(response)
    return {"ok": True}


@auth_router.post("/logout-all")
async def logout_all(request: Request, response: Response, user=Depends(get_current_user), db=Depends(get_db)):
    from app.auth.sessions import revoke_user_sessions
    await revoke_user_sessions(db, user.id)
    record_auth_event(db, request, "logout_all", user)
    await db.commit()
    clear_auth_cookies(response)
    return {"ok": True}


@auth_router.get("/{provider}/login")
async def oauth_login(provider: str, request: Request, db=Depends(get_db)):
    response = RedirectResponse("/", status_code=302)
    response.headers["Location"] = await begin_oauth(db, request, response, provider)
    return response


@auth_router.get("/{provider}/callback")
async def oauth_callback(provider: str, request: Request, state: str = Query(default="", max_length=128),
                         code: str = Query(default="", max_length=4096), db=Depends(get_db)):
    base = request.app.state.config["URLS"]["FrontendUrl"]
    response = RedirectResponse(base + "/loading?session=1", status_code=302)
    try:
        await complete_oauth(db, request, response, provider, state, code)
    except Exception as error:
        await db.rollback()
        reason = "error"
        if isinstance(error, HTTPException) and isinstance(error.detail, dict):
            if error.detail.get("code") in {"account_link_required", "oauth_state_invalid", "auth_rate_limited"}:
                reason = error.detail["code"]
        structlog.get_logger(__name__).warning("oauth_rejected", error_type=type(error).__name__)
        # Discard all tentative auth cookies if the transaction did not commit.
        response = RedirectResponse(base + "/login?why=" + reason, status_code=302)
    response.delete_cookie(OAUTH_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return response
