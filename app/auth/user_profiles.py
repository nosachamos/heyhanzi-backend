from sqlalchemy import select

from app.auth.permissions import is_admin
from app.models.auth import UserAuthIdentity


def serialize_user_profile(user, config):
    admin = is_admin(user, config)
    return {
        "id": user.id, "createdAt": user.created_at * 1000,
        "name": user.name, "email": user.email_normalized, "profilePictureUrl": user.profile_picture_url,
        "userType": "free", "isSuperAdmin": admin,
        "role": "admin" if admin else "learner",
        "emailVerifiedAt": user.email_verified_at,
        "emailVerificationSource": user.email_verification_source,
        "hasPassword": user.password_hash is not None,
    }


async def account_security_summary(db, user):
    providers = list(await db.scalars(select(UserAuthIdentity.provider).where(UserAuthIdentity.user_id == user.id)))
    return {
        "email": user.email_normalized, "emailVerifiedAt": user.email_verified_at,
        "emailVerificationSource": user.email_verification_source,
        "hasPassword": user.password_hash is not None, "passwordAuthAllowed": True,
        "passwordUpdatedAt": user.password_updated_at,
        "authMethods": (["password"] if user.password_hash else []) + sorted(providers),
    }
