"""Durable studies; only explicit activation changes anonymous learner feedback."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.success_feedback import SuccessFeedbackPreset, SuccessFeedbackState
from app.utils.time import utc_now_seconds

MAX_PRESETS = 100


def failure(status, code):
    return HTTPException(status, detail={'code': code}, headers={'Cache-Control': 'no-store'})


def serialize(preset):
    return {
        'id': preset.id, 'name': preset.name, 'version': preset.version,
        'configuration': deepcopy(preset.configuration),
        'createdAt': preset.created_at, 'updatedAt': preset.updated_at,
    }


async def state_row(db, *, write=False):
    query = select(SuccessFeedbackState).where(SuccessFeedbackState.id == 'global')
    # Every admin mutation takes this lock first; catalog reads share-lock it
    # while collecting rows. Nothing is lazily created by a read request.
    query = query.with_for_update() if write else query.with_for_update(read=True)
    row = await db.scalar(query)
    if row is None:
        raise failure(503, 'success_feedback_unavailable')
    return row


async def public_state(db):
    # A single query reads the entire activation value. No auth/session lookup,
    # preference, CSRF token, account field or mutable preset is exposed here.
    row = (await db.execute(select(
        SuccessFeedbackState.version, SuccessFeedbackState.active_snapshot,
    ).where(SuccessFeedbackState.id == 'global'))).one_or_none()
    if row is None:
        raise failure(503, 'success_feedback_unavailable')
    return {'version': row.version, 'active': deepcopy(row.active_snapshot)}


async def catalog_value(db, state):
    rows = await db.scalars(select(SuccessFeedbackPreset).order_by(
        SuccessFeedbackPreset.created_at, SuccessFeedbackPreset.id,
    ))
    return {
        'version': state.version, 'active': deepcopy(state.active_snapshot),
        'presets': [serialize(preset) for preset in rows],
    }


async def catalog(db):
    return await catalog_value(db, await state_row(db))


async def save_preset(db, user, preset_id, payload):
    state = await state_row(db, write=True)
    preset = await db.get(SuccessFeedbackPreset, preset_id)
    configuration = payload.configuration.model_dump()
    now = utc_now_seconds()
    if preset is None:
        if payload.expectedVersion != 0:
            raise failure(409, 'success_feedback_preset_changed')
        count = await db.scalar(select(func.count()).select_from(SuccessFeedbackPreset))
        if count >= MAX_PRESETS:
            raise failure(409, 'success_feedback_preset_limit')
        preset = SuccessFeedbackPreset(
            id=preset_id, name=payload.name, version=1,
            configuration=configuration, created_at=now, updated_at=now,
            updated_by=user.id,
        )
        db.add(preset)
    else:
        if payload.expectedVersion != preset.version:
            raise failure(409, 'success_feedback_preset_changed')
        if preset.name != payload.name or preset.configuration != configuration:
            preset.name = payload.name
            preset.configuration = configuration
            preset.version += 1
            preset.updated_at = now
            preset.updated_by = user.id
    # Do not touch the activation snapshot, even when this preset is active.
    await db.flush()
    result = await catalog_value(db, state)
    await db.commit()
    return result


async def activate_preset(db, user, payload):
    state = await state_row(db, write=True)
    if payload.expectedVersion != state.version:
        raise failure(409, 'success_feedback_activation_changed')
    preset = await db.get(SuccessFeedbackPreset, payload.presetId)
    if preset is None:
        raise failure(404, 'success_feedback_preset_not_found')
    if preset.version != payload.expectedPresetVersion:
        raise failure(409, 'success_feedback_preset_changed')
    snapshot = serialize(preset)
    if state.active_snapshot != snapshot:
        state.active_snapshot = snapshot
        state.version += 1
        state.updated_at = utc_now_seconds()
        state.activated_by = user.id
    await db.flush()
    result = await catalog_value(db, state)
    await db.commit()
    return result
