from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from app.auth.permissions import authorize_preview
from app.auth.sessions import get_verified_user
from app.database.database import get_db
from app.schemas.course_exercise import CourseCheck, CourseImport, CourseSave, GuestCourseRequest
from app.services import course_exercises, course_sets


async def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


async def set_receipt_context(request: Request):
    token = course_sets.signing_key.set(request.app.state.config['AUTH']['SecretKey'])
    scope = course_sets.receipt_scope.set('preview' if getattr(request.state, 'development_preview', False) else 'learner')
    try:
        yield
    finally:
        course_sets.receipt_scope.reset(scope)
        course_sets.signing_key.reset(token)


course_exercise_router = APIRouter(prefix="/learning/course", tags=["learning"], dependencies=[Depends(no_cache), Depends(set_receipt_context)])


@course_exercise_router.get("")
async def state(user=Depends(get_verified_user), db=Depends(get_db)):
    return await course_exercises.state(db, user.id)


@course_exercise_router.get("/exercises/{instance_id}")
async def exercise(instance_id: str = Path(min_length=1, max_length=96),
                   run_id: UUID | None = None,
                   user=Depends(get_verified_user), db=Depends(get_db)):
    return await course_exercises.detail(db, user.id, instance_id, run_id=run_id)


@course_exercise_router.put("/runs/{run_id}")
async def save(run_id: UUID, payload: CourseSave, user=Depends(get_verified_user), db=Depends(get_db)):
    return await course_exercises.save(db, user.id, run_id, payload)


@course_exercise_router.post("/runs/{run_id}/checks")
async def check(run_id: UUID, payload: CourseCheck, user=Depends(get_verified_user), db=Depends(get_db)):
    return await course_exercises.check(db, user.id, run_id, payload)


@course_exercise_router.post("/guest", dependencies=[Depends(authorize_preview)])
async def guest(payload: GuestCourseRequest, request: Request, db=Depends(get_db)):
    return await course_exercises.guest_request(db, payload, preview=getattr(request.state, 'development_preview', False))


@course_exercise_router.post("/import")
async def import_guest(payload: CourseImport, user=Depends(get_verified_user), db=Depends(get_db)):
    return await course_exercises.import_guest(db, user.id, payload)
