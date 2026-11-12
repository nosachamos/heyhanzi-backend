from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import FileResponse

from app.auth.permissions import authorize_preview
from app.auth.sessions import get_verified_user
from app.database.database import get_db
from app.schemas.learning import (
    EnrollmentRequest,
    GuestImportRequest,
    GuestLessonRequest,
    VocabularySeenRequest,
)
from app.services import guest_learning, learning, pinyin_learning, pronunciation


async def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


learning_router = APIRouter(prefix="/learning", tags=["learning"], dependencies=[Depends(no_cache)])


@learning_router.get("/tracks")
async def tracks(db=Depends(get_db)):
    return {"tracks": await learning.available_tracks(db)}


@learning_router.get("/enrollments")
async def enrollments(user=Depends(get_verified_user), db=Depends(get_db)):
    return {"trackIds": await learning.enrollments(db, user.id)}


@learning_router.post("/enrollments")
async def enroll(payload: EnrollmentRequest, user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.enroll(db, user.id, payload.trackId)


@learning_router.get("/tracks/{track_id}/path")
async def path(track_id: str, user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.learning_path(db, user.id, track_id)


@learning_router.post("/tracks/{track_id}/blocks/{block_id}/complete")
async def complete(track_id: str, block_id: str, user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.complete_word(db, user.id, track_id, block_id)


@learning_router.get("/strokes/{language_tag}/{character}/{version}")
async def strokes(language_tag: str = Path(min_length=2, max_length=32),
                  character: str = Path(min_length=1, max_length=1),
                  version: str = Path(pattern=r"^[0-9a-f]{64}$"),
                  block_id: str = Query(alias="blockId", min_length=1, max_length=96),
                  user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.character_strokes(db, user.id, language_tag, character, version, block_id)


@learning_router.get("/audio/{language_tag}/{audio_id}/{version}", response_class=FileResponse)
async def audio(language_tag: str = Path(min_length=2, max_length=32),
                audio_id: str = Path(pattern=r"^[a-z0-9-]{1,96}$"),
                version: str = Path(pattern=r"^[0-9a-f]{64}$"),
                user=Depends(get_verified_user)):
    return FileResponse(pronunciation.audio_file(language_tag, audio_id, version), media_type="audio/mpeg",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@learning_router.get("/tracks/{track_id}/journey")
async def journey(track_id: str, level: str | None = Query(default=None, max_length=32),
                  around: str | None = Query(default=None, max_length=96),
                  before: int | None = Query(default=None, ge=1), after: int | None = Query(default=None, ge=1),
                  limit: int = Query(default=20, ge=1, le=40),
                  user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.journey(db, user.id, track_id, level=level, around=around, before=before, after=after, limit=limit)


@learning_router.get("/tracks/{track_id}/blocks/{block_id}/vocabulary")
async def common_vocabulary(track_id: str, block_id: str, after: str | None = Query(default=None, max_length=96),
                            limit: int = Query(default=20, ge=1, le=40),
                            user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.common_vocabulary(db, user.id, track_id, block_id, after=after, limit=limit)


@learning_router.post("/tracks/{track_id}/blocks/{block_id}/vocabulary/seen")
async def vocabulary_seen(track_id: str, block_id: str, payload: VocabularySeenRequest,
                          user=Depends(get_verified_user), db=Depends(get_db)):
    return await learning.record_vocabulary_seen(db, user.id, track_id, block_id,
        payload.vocabularyIds, payload.grammar, payload.lexicalUses)


@learning_router.post('/guest/lesson', dependencies=[Depends(authorize_preview)])
async def guest_lesson(payload: GuestLessonRequest, request: Request, db=Depends(get_db)):
    return await guest_learning.lesson(db, payload, preview=getattr(request.state, 'development_preview', False))


@learning_router.get('/guest/audio/{language_tag}/{audio_id}/{version}', response_class=FileResponse)
async def guest_audio(language_tag: str = Path(min_length=2, max_length=32),
                      audio_id: str = Path(pattern=r'^[a-z0-9-]{1,96}$'),
                      version: str = Path(pattern=r'^[0-9a-f]{64}$')):
    return FileResponse(pronunciation.audio_file(language_tag, audio_id, version), media_type='audio/mpeg',
                        headers={'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff'})


@learning_router.post('/guest/import')
async def import_guest(payload: GuestImportRequest, user=Depends(get_verified_user), db=Depends(get_db)):
    return await guest_learning.import_progress(db, user.id, payload)


@learning_router.post("/tracks/{track_id}/pinyin/{section}/visit")
async def pinyin_visit(track_id: str, section: str, user=Depends(get_verified_user), db=Depends(get_db)):
    return await pinyin_learning.visit(db, user.id, track_id, section)
