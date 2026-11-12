"""Live approval overlay; immutable audio, original failures and timing stay intact."""
import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.audio_review import AudioReviewDecision, AudioReviewEvent
from app.utils.time import utc_now_seconds

DIRECTORY = Path(__file__).resolve().parents[1] / 'data/sentence-audio/review-catalog'


@lru_cache(maxsize=8)
def read_json(path, modified, size):
    return json.loads(path.read_text())


def document(path):
    stat = path.stat()
    return read_json(path, stat.st_mtime_ns, stat.st_size)


def active_catalog():
    try:
        active = document(DIRECTORY / 'active.json')
        release = document(DIRECTORY.parent / 'active.json')['releaseId']
        if active['releaseId'] != release:
            raise ValueError('Review catalog does not match active audio')
        return active
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, detail={'code': 'audio_review_unavailable'}) from None


def recording(review_key):
    active = active_catalog()
    if not re.fullmatch('[a-f0-9]{64}', review_key) or review_key not in active['keys']:
        raise HTTPException(404, detail={'code': 'audio_review_recording_not_found'})
    return document(DIRECTORY / active['releaseId'] / (review_key[:2] + '.json'))[review_key]


def serialize(row):
    return {'approved': row.approved, 'revision': row.revision, 'reviewedAt': row.reviewed_at}


async def state(db):
    active = active_catalog()
    keys = set(active['keys'])
    rows = await db.scalars(select(AudioReviewDecision))
    return {'catalogReleaseId': active['releaseId'],
            'decisions': {r.review_key: serialize(r) for r in rows if r.review_key in keys}}


async def decide(db, user, review_key, payload):
    source = recording(review_key)
    if source['sha256'] != payload.sha256:
        raise HTTPException(409, detail={'code': 'audio_review_recording_changed'})
    await db.execute(insert(AudioReviewDecision).values(
        review_key=review_key, approved=False, revision=0, reviewed_at=0
    ).on_conflict_do_nothing(index_elements=['review_key']))
    row = await db.scalar(select(AudioReviewDecision).where(
        AudioReviewDecision.review_key == review_key).with_for_update())
    if row.revision != payload.expectedRevision:
        raise HTTPException(409, detail={'code': 'audio_review_changed'})
    if row.approved != payload.approved:
        row.approved = payload.approved
        row.revision += 1
        row.reviewed_at = utc_now_seconds()
        db.add(AudioReviewEvent(review_key=review_key, reviewer_id=user.id,
            approved=row.approved, revision=row.revision, reviewed_at=row.reviewed_at, recording=source))
    await db.commit()
    return serialize(row)
