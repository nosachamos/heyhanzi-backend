"""Shared review state and authenticated, CSRF-protected reviewer decisions."""
from fastapi import APIRouter, Depends, Request, Response

from app.auth.permissions import get_admin_user
from app.database.database import get_db
from app.schemas.audio_review import ReviewRequest
from app.services import audio_review

router = APIRouter(prefix='/learning/audio-review', tags=['audio-review'], dependencies=[Depends(get_admin_user)])


@router.get('/state')
async def review_state(response: Response, db=Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return await audio_review.state(db)


@router.get('/access')
async def reviewer_access(request: Request, response: Response, user=Depends(get_admin_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'canReview': True}


@router.put('/recordings/{review_key}')
async def review_recording(review_key: str, payload: ReviewRequest, request: Request,
                           response: Response, user=Depends(get_admin_user), db=Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return await audio_review.decide(db, user, review_key, payload)
