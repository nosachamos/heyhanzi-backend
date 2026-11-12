"""One server-owned admin role shared by every development dashboard tool."""
from fastapi import Depends, HTTPException, Request, Response

from app.auth.sessions import get_current_user, get_verified_user
from app.database.database import get_db


def is_admin(user, config):
    emails = config.get('ADMIN', {}).get('Emails', [])
    return bool(user.email_verified_at is not None and user.disabled_at is None
                and isinstance(emails, list)
                and user.email_normalized in {str(email).strip().casefold() for email in emails})


def require_admin(user, config):
    if not is_admin(user, config):
        raise HTTPException(403, detail={'code': 'admin_required'}, headers={'Cache-Control': 'no-store'})


async def get_admin_user(request: Request, user=Depends(get_verified_user)):
    require_admin(user, request.app.state.config)
    return user


async def authorize_preview(request: Request, response: Response, db=Depends(get_db)):
    """A local debug header never grants an anonymous caller admin privileges."""
    if getattr(request.state, 'development_preview', False):
        user = await get_current_user(request, response, db)
        require_admin(user, request.app.state.config)
