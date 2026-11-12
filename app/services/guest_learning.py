"""Read-only guest lessons and an additive, atomic account import.

Guest state never creates users, sessions or enrollment rows. The regular lesson
serializers and vocabulary eligibility rules serve both learning modes.
"""

from dataclasses import dataclass, field

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert

from app.models.learning import (
    LearningEnrollment,
    LearningStage,
    Vocabulary,
    VocabularyCharacter,
    WordBlock,
    WordCompletion,
)
from app.services import learning


@dataclass
class GuestState:
    track_id: str
    completed: set[str]
    seen: set[str]
    exposures: dict = field(default_factory=dict)
    grammar_exposures: dict = field(default_factory=dict)
    preview: bool = False
    pinyin_completed: list[str] = field(default_factory=list)
    lexical_use_exposures: dict = field(default_factory=dict)
    historical_pending: set[str] = field(default_factory=set)


async def validate_progress(db, payload, *, preview=False):
    if payload.developmentPreview and not preview:
        raise learning.learning_error(422, "development_preview_progress_not_importable")
    from app.services.pinyin_learning import validate_visits

    pinyin = validate_visits(payload.pinyinCompleted)
    track = await learning.get_track(db, payload.trackId)
    if pinyin and not track.language_tag.startswith("cmn"):
        raise learning.learning_error(422, "learning_invalid_pinyin_progress")
    completed = set(payload.completed)
    # Visits are a prefix of the actual curriculum, with optional story markers.
    # Never accept unknown IDs, out-of-order lessons or another track's progress.
    rows = (
        await db.execute(
            select(WordBlock.id, WordBlock.kind, WordBlock.language_data)
            .join(LearningStage)
            .where(LearningStage.track_id == payload.trackId, WordBlock.id.in_(completed))
        )
    ).all()
    if {row.id for row in rows} != completed:
        raise learning.learning_error(422, "learning_invalid_guest_progress")
    words = {row.id for row in rows if row.kind == "word"}
    ordered = (
        list(
            await db.scalars(
                select(WordBlock)
                .join(LearningStage)
                .where(LearningStage.track_id == payload.trackId, WordBlock.kind == "word")
                .order_by(WordBlock.sequence, LearningStage.position, WordBlock.position)
            )
        )
        if words
        else []
    )

    def contained_prefix(blocks):
        result = set()
        for block in blocks:
            if block.id not in words:
                break
            result.add(block.id)
        return result

    prefix = contained_prefix(ordered)
    historical_pending = set()
    if track.curriculum and track.curriculum.get("previous_ordering"):
        # A forward reorder retains genuine old visits. Subsequent visits fill
        # the earliest new gap; they never need to erase the previous prefix.
        historical = sorted(
            ordered,
            key=lambda b: b.language_data.get("previous_notebook_order", {}).get(
                "sequence", float("inf")
            ),
        )
        prefix |= contained_prefix(historical)
        old_next = next((block for block in historical if block.id not in words), None)
        if old_next is not None:
            historical_pending.add(old_next.id)
    if not preview and (
        words != prefix
        or any(
            row.kind == "story"
            and not set(row.language_data.get("requiredBlockIds", [])).issubset(words)
            for row in rows
        )
    ):
        raise learning.learning_error(422, "learning_invalid_guest_progress")
    guest = GuestState(
        payload.trackId,
        completed,
        {word for ids in payload.exposures.values() for word in ids},
        payload.exposures,
        payload.grammarExposures,
        preview,
        pinyin,
        payload.lexicalUseExposures,
        historical_pending,
    )
    if guest.seen:
        from app.services import notebook_grammar

        next_word = await learning.next_word_for(db, guest)
        _, current = await notebook_grammar.lexical_evidence(db, guest=guest, block=next_word)
        # Keep all raw receipts in `exposures`, while examples see only actual
        # current displays. A stale source position or former next-card peek
        # must neither break reload nor introduce a newly authored word/use.
        guest.seen.intersection_update(current)
    return guest


async def lesson(db, payload, *, preview=False):
    guest = await validate_progress(db, payload, preview=preview)
    if payload.operation == "journey":
        return await learning.journey(
            db,
            None,
            payload.trackId,
            level=payload.level,
            around=payload.around,
            before=payload.before,
            after=payload.after,
            guest=guest,
        )
    if not payload.blockId:
        raise learning.learning_error(422, "learning_block_required")
    if payload.operation == "vocabulary":
        return await learning.common_vocabulary(
            db, None, payload.trackId, payload.blockId, after=payload.vocabularyAfter, guest=guest
        )
    if payload.reference is None:
        raise learning.learning_error(422, "learning_strokes_not_found")
    ref = payload.reference
    return await learning.character_strokes(
        db, None, ref.languageTag, ref.character, ref.version, payload.blockId, guest=guest
    )


async def import_progress(db, user_id, payload):
    if payload.accountId != user_id:
        raise learning.learning_error(409, "learning_account_changed")
    guest = await validate_progress(db, payload)
    # Validate every claimed exposure before writing anything. A character visit
    # never manufactures a vocabulary exposure. Merge the earliest valid source
    # position without losing any existing account evidence.
    exposure_rows = []
    for block_id, ids in payload.exposures.items():
        if not ids:
            continue
        if block_id in guest.historical_pending and block_id not in guest.completed:
            continue  # An unvisited old next card cannot introduce account knowledge.
        track, context, block, known, introduced = await learning.vocabulary_context(
            db, None, payload.trackId, block_id, guest=guest
        )
        if block.kind != "word":
            raise learning.learning_error(422, "learning_invalid_vocabulary")
        # An earlier support word can disappear from today's card plan as the
        # learner advances. Validate explicit historical readings against known
        # characters, without inventing exposure from character visits alone.
        unknown = exists(
            select(VocabularyCharacter.vocabulary_id).where(
                VocabularyCharacter.vocabulary_id == Vocabulary.id,
                VocabularyCharacter.character.not_in(known),
            )
        )
        candidates = list(
            await db.scalars(
                select(Vocabulary).where(
                    Vocabulary.id.in_(ids), Vocabulary.language_tag == track.language_tag, ~unknown
                )
            )
        )
        if {word.id for word in candidates} != set(ids) or any(
            not learning.vocabulary_display_current(word, block.sequence)
            and not (
                track.curriculum
                and track.curriculum.get("previous_ordering")
                and block.language_data.get("previous_notebook_order")
                and "previous_display_metadata" in word.language_data
            )
            for word in candidates
        ):
            raise learning.learning_error(422, "learning_invalid_vocabulary")
        exposure_rows.extend((word, block_id, block.sequence) for word in set(ids))
    from app.services import notebook_grammar, notebook_lexical_progress, notebook_lexical_uses

    grammar = await notebook_grammar.guest_evidence(db, guest)
    # Stale receipt history remains in the browser. Import only exact current
    # scope displays with complete earlier lexical support.
    next_word = await learning.next_word_for(db, guest)
    blocks, sources = await notebook_grammar.lexical_evidence(db, guest=guest, block=next_word)
    lexical = {}
    for block_id, refs in guest.lexical_use_exposures.items():
        block = blocks.get(block_id)
        if block is None:
            if block_id in guest.historical_pending:
                continue
            raise learning.learning_error(422, "learning_invalid_vocabulary")
        selected = []
        for ref in refs:
            source = sources.get(ref.id)
            if (
                source is not None
                and notebook_lexical_uses.reference(source[0])
                == {"id": ref.id, "fingerprint": ref.fingerprint}
                and source[1] == block.sequence
            ):
                selected.append(ref)
        if selected:
            lexical[block_id] = selected
    await learning.ensure_enrollment(db, user_id, payload.trackId)
    enrollment = await db.scalar(
        select(LearningEnrollment)
        .where(
            LearningEnrollment.user_id == user_id, LearningEnrollment.track_id == payload.trackId
        )
        .with_for_update()
    )
    from app.services.pinyin_learning import validate_visits

    enrollment.pinyin_completed = validate_visits(
        set(enrollment.pinyin_completed) | set(guest.pinyin_completed)
    )
    if guest.completed:
        await db.execute(
            insert(WordCompletion)
            .values(
                [
                    {"enrollment_id": enrollment.id, "block_id": block_id}
                    for block_id in sorted(guest.completed)
                ]
            )
            .on_conflict_do_nothing(index_elements=["enrollment_id", "block_id"])
        )
    await learning.store_vocabulary_exposures(db, enrollment.id, exposure_rows)
    for block_id, refs in lexical.items():
        await notebook_lexical_progress.store(db, enrollment.id, blocks[block_id], refs)
    await notebook_grammar.store_displays(db, enrollment.id, grammar)
    await db.commit()
    return {"ok": True}
