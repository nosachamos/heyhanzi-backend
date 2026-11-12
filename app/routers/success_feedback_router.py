"""Anonymous activation read, with separate authenticated admin study actions."""
from fastapi import APIRouter, Depends, Path
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.auth.permissions import get_admin_user
from app.database.database import get_db
from app.schemas.success_feedback import ActivateSuccessPreset, SaveSuccessPreset
from app.services import success_feedback


class NoStoreRoute(APIRoute):
    """Keep both public live settings and admin responses outside HTTP caches."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def no_store(request):
            try:
                response = await original(request)
            except HTTPException as error:
                handler = request.app.exception_handlers.get(HTTPException, http_exception_handler)
                response = await handler(request, error)
            except RequestValidationError as error:
                # Reuse the app's input-redacting handler, including for 422s.
                response = await request.app.exception_handlers[RequestValidationError](request, error)
            response.headers['Cache-Control'] = 'no-store'
            return response

        return no_store


router = APIRouter(prefix='/learning/success-feedback', tags=['success-feedback'], route_class=NoStoreRoute)


@router.get('/active')
async def active(db=Depends(get_db)):
    return await success_feedback.public_state(db)


@router.get('/presets')
async def presets(user=Depends(get_admin_user), db=Depends(get_db)):
    return await success_feedback.catalog(db)


@router.put('/presets/{preset_id}')
async def save(payload: SaveSuccessPreset,
               preset_id: str = Path(pattern=r'^[a-z0-9][a-z0-9-]{0,63}$'),
               user=Depends(get_admin_user), db=Depends(get_db)):
    return await success_feedback.save_preset(db, user, preset_id, payload)


@router.put('/active')
async def activate(payload: ActivateSuccessPreset,
                   user=Depends(get_admin_user), db=Depends(get_db)):
    return await success_feedback.activate_preset(db, user, payload)
