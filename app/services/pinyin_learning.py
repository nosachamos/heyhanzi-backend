"""Reading visits only. These never grant vocabulary, grammar or pronunciation mastery."""
from sqlalchemy import select

from app.models.learning import LearningEnrollment
from app.services.learning import get_track, learning_error

SECTIONS = ('welcome', 'sounds', 'tones', 'spelling')


def validate_visits(values):
    values = set(values)
    if values != set(SECTIONS[:len(values)]):
        raise learning_error(422, 'learning_invalid_pinyin_progress')
    return [section for section in SECTIONS if section in values]


async def visit(db, user_id, track_id, section):
    track = await get_track(db, track_id)
    if section not in SECTIONS or not track.language_tag.startswith('cmn'):
        raise learning_error(404, 'learning_pinyin_not_found')
    enrollment = await db.scalar(select(LearningEnrollment).where(
        LearningEnrollment.user_id == user_id, LearningEnrollment.track_id == track_id).with_for_update())
    if enrollment is None:
        raise learning_error(404, 'learning_enrollment_required')
    completed = set(enrollment.pinyin_completed)
    if not set(SECTIONS[:SECTIONS.index(section)]).issubset(completed):
        raise learning_error(409, 'learning_pinyin_locked')
    enrollment.pinyin_completed = validate_visits(completed | {section})
    result = list(enrollment.pinyin_completed)
    await db.commit()
    return {'pinyinCompleted': result}
