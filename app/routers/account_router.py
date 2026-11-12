from fastapi import APIRouter, Depends, Request, Response

from app.auth.account_passwords import update_password
from app.auth.email_flows import locked_user, resend_verification
from app.auth.oauth import begin_oauth
from app.auth.sessions import get_current_user
from app.auth.user_profiles import account_security_summary, serialize_user_profile
from app.database.database import get_db
from app.schemas.auth import (
    ChangePasswordRequest,
    ProfileRequest,
    SetPasswordRequest,
    TurnstileRequest,
)

account_router = APIRouter(prefix="/account", tags=["account"])


@account_router.put("/profile")
async def update_profile(payload: ProfileRequest, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    user = await locked_user(db, user.id)
    user.name = payload.name
    await db.commit()
    return serialize_user_profile(user, request.app.state.config)


@account_router.get("/security")
async def get_security(user=Depends(get_current_user), db=Depends(get_db)):
    return await account_security_summary(db, user)


@account_router.post("/email-verification/resend")
async def resend(payload: TurnstileRequest, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    return await resend_verification(db, request, user, payload.turnstileToken)


@account_router.post("/password/change")
async def change_password(payload: ChangePasswordRequest, request: Request, response: Response,
                          user=Depends(get_current_user), db=Depends(get_db)):
    return await update_password(db, request, response, user, payload.newPassword, current_password=payload.currentPassword)


@account_router.post("/password/set")
async def set_password(payload: SetPasswordRequest, request: Request, response: Response,
                       user=Depends(get_current_user), db=Depends(get_db)):
    return await update_password(db, request, response, user, payload.password)


@account_router.post("/auth/{provider}/link")
async def link_provider(provider: str, request: Request, response: Response,
                        user=Depends(get_current_user), db=Depends(get_db)):
    return {"authorizationUrl": await begin_oauth(db, request, response, provider, user=user)}
