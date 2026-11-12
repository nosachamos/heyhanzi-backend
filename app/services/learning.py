
"""Learning operations. A locked enrollment serializes advancement across tabs."""

from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import and_, case, exists, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.models.learning import (
    BlockCharacter,
    CharacterStrokes,
    LearningEnrollment,
    LearningLevel,
    LearningStage,
    LearningTrack,
    Vocabulary,
    VocabularyCharacter,
    VocabularyExposure,
    WordBlock,
    WordCompletion,
)
from app.services import learning_glosses, notebook_lexical_uses
from app.services.billing import has_access, require_access
from app.services.learning_examples import (
    example_sentence,
    is_word,
    preferred_entries,
    required_vocabulary_ids,
    required_words,
    sentence_examples,
    vocabulary_identity,
)
from app.services.learning_glosses import (
    primary_pronunciation,
    primary_translations,
    word_translations,
)
from app.services.primary_character_contexts import for_word as character_contexts_for_word
from app.services.sentence_audio import pronunciation as audio_pronunciation


def learning_error(status, code):
    return HTTPException(status, detail={"code": code})


def serialize_track(track):
    return {"id": track.id, "languageTag": track.language_tag, "names": track.names,
            "version": track.version, "framework": track.framework, "curriculum": track.curriculum}


async def available_tracks(db):
    return [serialize_track(track) for track in await db.scalars(select(LearningTrack).order_by(LearningTrack.id))]


async def get_track(db, track_id):
    track = await db.get(LearningTrack, track_id)
    if track is None:
        raise learning_error(404, "learning_track_not_found")
    return track


async def ensure_enrollment(db, user_id, track_id):
    """Participates in the caller's signup/enrollment transaction; never commits it."""
    await get_track(db, track_id)
    await db.execute(insert(LearningEnrollment).values(user_id=user_id, track_id=track_id)
                     .on_conflict_do_nothing(index_elements=["user_id", "track_id"]))


async def enroll(db, user_id, track_id):
    await ensure_enrollment(db, user_id, track_id)
    await db.commit()
    return await learning_path(db, user_id, track_id)


async def enrollments(db, user_id):
    return list(await db.scalars(select(LearningEnrollment.track_id)
                .where(LearningEnrollment.user_id == user_id).order_by(LearningEnrollment.created_at, LearningEnrollment.id)))


async def ordered_words(db, track_id):
    return list(await db.scalars(select(WordBlock).join(LearningStage)
                .where(LearningStage.track_id == track_id, LearningStage.kind == "warmup", WordBlock.kind == "word")
                .order_by(LearningStage.position, WordBlock.position)))


async def learning_path(db, user_id, track_id, *, guest=None):
    track = await get_track(db, track_id)
    enrollment = await get_enrollment(db, user_id, track_id) if guest is None else None
    completed = guest.completed if guest is not None else set(await db.scalars(select(WordCompletion.block_id).where(WordCompletion.enrollment_id == enrollment.id)))
    words = await ordered_words(db, track_id)
    next_id = next((word.id for word in words if word.id not in completed), None)
    stages = list(await db.scalars(select(LearningStage).where(LearningStage.track_id == track_id, LearningStage.kind == "warmup").order_by(LearningStage.position)))
    return {"pinyinCompleted": guest.pinyin_completed if guest is not None else enrollment.pinyin_completed, "track": serialize_track(track), "completedCount": sum(word.id in completed for word in words), "totalCount": len(words), "nextBlockId": next_id,
            "stages": [{"id": stage.id, "kind": stage.kind, "names": stage.names, "level": stage.level,
                        "blocks": [{"id": word.id, "kind": "word", "position": word.position, "text": word.text,
                                    "translations": primary_translations(word.translations, text=word.text, pronunciation=word.pronunciation), "pronunciation": audio_pronunciation(word.text, primary_pronunciation(word.text, word.pronunciation)),
                                    "languageData": word.language_data,
                                    "status": "completed" if word.id in completed else "next" if word.id == next_id or (guest is not None and guest.preview) else "locked"}
                                   for word in words if word.stage_id == stage.id]} for stage in stages]}


async def complete_word(db, user_id, track_id, block_id):
    enrollment = await db.scalar(select(LearningEnrollment).where(
        LearningEnrollment.user_id == user_id, LearningEnrollment.track_id == track_id).with_for_update())
    if enrollment is None:
        raise learning_error(404, "learning_enrollment_required")
    block = await db.scalar(select(WordBlock).join(LearningStage).where(
        LearningStage.track_id == track_id, WordBlock.id == block_id))
    if block is None:
        raise learning_error(404, "learning_block_not_found")
    completed = set(await db.scalars(select(WordCompletion.block_id).where(WordCompletion.enrollment_id == enrollment.id)))
    if block_id not in completed:
        if block.kind == "story":
            allowed = set(block.language_data.get("requiredBlockIds", [])).issubset(completed)
        else:
            next_word = await next_word_for(db, enrollment)
            allowed = next_word is not None and block_id == next_word.id
        if not allowed:
            raise learning_error(409, "learning_block_locked")
        if block.kind == "word":
            from app.services.course_exercises import require_notebook_step
            await require_notebook_step(db, block, enrollment=enrollment)
        db.add(WordCompletion(enrollment_id=enrollment.id, block_id=block_id))
    await db.commit()
    return await learning_path(db, user_id, track_id)


async def character_strokes(db, user_id, language_tag, character, version, block_id, *, guest=None):
    track = await db.scalar(select(LearningTrack).join(LearningStage).join(WordBlock).where(
        WordBlock.id == block_id))
    if track is None:
        raise learning_error(404, "learning_block_not_found")
    enrollment = await get_enrollment(db, user_id, track.id) if guest is None else guest
    if guest is not None and track.id != guest.track_id:
        raise learning_error(404, "learning_block_not_found")
    block, stage = (await db.execute(select(WordBlock, LearningStage).join(LearningStage).where(
        WordBlock.id == block_id, LearningStage.track_id == track.id))).one()
    references = block.language_data.get("strokeOrder", [])
    if block.kind != "word" or not any(
        ref.get("languageTag") == language_tag and ref.get("character") == character and ref.get("version") == version
        for ref in references if isinstance(ref, dict)
    ):
        raise learning_error(404, "learning_strokes_not_found")
    visited = (True if block_id in guest.completed else None) if guest is not None else await db.get(WordCompletion, (enrollment.id, block_id))
    next_word = await next_word_for(db, enrollment)
    preview = guest is not None and guest.preview
    if not preview and visited is None and (next_word is None or next_word.id != block_id):
        raise learning_error(409, "learning_block_locked")
    if stage.kind != "warmup" and not preview:
        if guest is not None:
            raise learning_error(403, "subscription_required")
        await require_access(db, user_id)
    resource = await db.get(CharacterStrokes, (language_tag, character, version))
    if resource is None:
        raise learning_error(404, "learning_strokes_not_found")
    return {"reference": {"format": "stroke-reference-v1", "languageTag": language_tag,
                          "character": character, "version": version}, "data": resource.data}


async def get_enrollment(db, user_id, track_id):
    enrollment = await db.scalar(select(LearningEnrollment).where(
        LearningEnrollment.user_id == user_id, LearningEnrollment.track_id == track_id))
    if enrollment is None:
        raise learning_error(404, "learning_enrollment_required")
    return enrollment


async def next_word_for(db, enrollment):
    completed = WordBlock.id.in_(enrollment.completed) if hasattr(enrollment, "completed") else exists(select(WordCompletion.block_id).where(
        WordCompletion.enrollment_id == enrollment.id, WordCompletion.block_id == WordBlock.id))
    return await db.scalar(select(WordBlock).join(LearningStage).where(
        LearningStage.track_id == enrollment.track_id, WordBlock.kind == "word", ~completed)
        .order_by(WordBlock.sequence, LearningStage.position, WordBlock.position).limit(1))


def serialize_block(block, stage, completed, next_id, *, preview=False):
    available_story = block.kind == "story" and set(block.language_data.get("requiredBlockIds", [])).issubset(completed)
    return {"id": block.id, "kind": block.kind, "position": block.position, "sequence": block.sequence,
            "unitNumber": stage.unit_number, "levelId": stage.level or "warmup", "text": block.text,
            "translations": primary_translations(block.translations, text=block.text, pronunciation=block.pronunciation), "pronunciation": audio_pronunciation(block.text, primary_pronunciation(block.text, block.pronunciation)), "languageData": block.language_data,
            "status": "completed" if block.id in completed else "next" if block.id == next_id or available_story or preview else "locked"}


async def journey(db, user_id, track_id, *, level=None, around=None, before=None, after=None, limit=20, guest=None):
    """Bounded, bidirectional activity window. Never materialize the full path."""
    if sum(value is not None for value in (around, before, after)) > 1:
        raise learning_error(422, "learning_invalid_cursor")
    track = await get_track(db, track_id)
    enrollment = await get_enrollment(db, user_id, track_id) if guest is None else guest
    next_word = await next_word_for(db, enrollment)
    next_stage = await db.get(LearningStage, next_word.stage_id) if next_word else None
    level_rows = list(await db.scalars(select(LearningLevel).where(LearningLevel.track_id == track_id).order_by(LearningLevel.position)))
    if not level_rows:
        # Compatibility for tracks that have not yet adopted levels.
        return await learning_path(db, user_id, track_id, guest=guest)
    selected = level or (next_stage.level or "warmup" if next_stage else level_rows[-1].key)
    if selected not in {item.key for item in level_rows}:
        raise learning_error(404, "learning_level_not_found")
    paid = await has_access(db, user_id) if guest is None else guest.preview
    completed = guest.completed if guest is not None else set(await db.scalars(select(WordCompletion.block_id).where(WordCompletion.enrollment_id == enrollment.id)))
    counts = (await db.execute(select(func.coalesce(LearningStage.level, "warmup"), func.count(WordBlock.id),
               func.sum(case((WordBlock.id.in_(completed), 1), else_=0)), func.count(func.distinct(LearningStage.id)),
               func.min(case((WordBlock.id.not_in(completed), WordBlock.sequence), else_=None)), func.max(WordBlock.sequence))
               .select_from(WordBlock).join(LearningStage)
               .where(LearningStage.track_id == track_id, WordBlock.kind == "word")
               .group_by(LearningStage.level))).all()
    statistics = {key: (total, visited, units, (first_missing - 1) if first_missing is not None else (last_sequence or 0))
                  for key, total, visited, units, first_missing, last_sequence in counts}
    statement = select(WordBlock, LearningStage).join(LearningStage).where(
        LearningStage.track_id == track_id, func.coalesce(LearningStage.level, "warmup") == selected)
    if before is not None:
        result = list((await db.execute(statement.where(WordBlock.sequence < before).order_by(WordBlock.sequence.desc()).limit(limit))).all())[::-1]
    elif after is not None:
        result = list((await db.execute(statement.where(WordBlock.sequence > after).order_by(WordBlock.sequence).limit(limit))).all())
    else:
        target = await db.scalar(statement.with_only_columns(WordBlock).where(WordBlock.id == around)) if around else None
        if around and target is None:
            raise learning_error(404, "learning_anchor_not_found")
        if target is None:
            if next_word and (next_stage.level or "warmup") == selected:
                target = next_word
            else:
                target = await db.scalar(statement.with_only_columns(WordBlock).where(WordBlock.kind == "word", WordBlock.id.not_in(completed)).order_by(WordBlock.sequence).limit(1))
                if target is None:
                    target = await db.scalar(statement.with_only_columns(WordBlock).order_by(WordBlock.sequence.desc()).limit(1))
        earlier = list((await db.execute(statement.where(WordBlock.sequence < target.sequence).order_by(WordBlock.sequence.desc()).limit(limit // 2))).all())[::-1] if target else []
        later = list((await db.execute(statement.where(WordBlock.sequence >= target.sequence).order_by(WordBlock.sequence).limit(limit - len(earlier)))).all()) if target else []
        result = earlier + later
    groups = {}
    for block, stage in result:
        if stage.id not in groups:
            groups[stage.id] = {"id": stage.id, "kind": stage.kind, "names": stage.names, "level": stage.level,
                                "unitNumber": stage.unit_number, "blocks": []}
        groups[stage.id]["blocks"].append(serialize_block(block, stage, completed, next_word.id if next_word else None,
                                                       preview=guest is not None and guest.preview))
    first, last = (result[0][0].sequence, result[-1][0].sequence) if result else (None, None)
    has_before = first is not None and await db.scalar(statement.with_only_columns(WordBlock.id).where(WordBlock.sequence < first).limit(1)) is not None
    has_after = last is not None and await db.scalar(statement.with_only_columns(WordBlock.id).where(WordBlock.sequence > last).limit(1)) is not None
    total, visited, _, _ = statistics.get(selected, (0, 0, 0, 0))
    return {"pinyinCompleted": guest.pinyin_completed if guest is not None else enrollment.pinyin_completed, "track": serialize_track(track), "hasUnlimitedAccess": paid, "levels": [{"id": item.key, "names": item.names,
                "totalCount": statistics.get(item.key, (0, 0, 0))[0], "completedCount": statistics.get(item.key, (0, 0, 0))[1],
                "unitCount": statistics.get(item.key, (0, 0, 0, 0))[2],
                "introducedThroughSequence": statistics.get(item.key, (0, 0, 0, 0))[3]} for item in level_rows],
            "selectedLevelId": selected, "nextLevelId": next_stage.level or "warmup" if next_stage else None,
            "completedCount": visited, "totalCount": total, "nextBlockId": next_word.id if next_word else None,
            "stages": list(groups.values()), "window": {"beforeCursor": first if has_before else None, "afterCursor": last if has_after else None}}


async def vocabulary_context(db, user_id, track_id, block_id, *, guest=None):
    track = await get_track(db, track_id)
    enrollment = await get_enrollment(db, user_id, track_id) if guest is None else guest
    block = await db.scalar(select(WordBlock).join(LearningStage).where(
        LearningStage.track_id == track_id, WordBlock.id == block_id))
    if block is None:
        raise learning_error(404, "learning_block_not_found")
    visited = (True if block_id in guest.completed else None) if guest is not None else await db.get(WordCompletion, (enrollment.id, block_id))
    next_word = await next_word_for(db, enrollment)
    preview = guest is not None and guest.preview
    if not preview and visited is None and (next_word is None or next_word.id != block_id):
        raise learning_error(409, "learning_block_locked")
    known_query = select(BlockCharacter.character, func.max(WordBlock.sequence)).select_from(BlockCharacter).join(WordBlock)
    if preview:
        # Content at this card uses its chronological prefix, not fabricated
        # learner visits or exposures. This context is never written/imported.
        known_query = known_query.join(LearningStage).where(
            LearningStage.track_id == track_id, WordBlock.sequence <= block.sequence)
    elif guest is not None:
        known_query = known_query.where(WordBlock.id.in_(guest.completed))
    else:
        known_query = known_query.join(WordCompletion, and_(WordCompletion.block_id == WordBlock.id, WordCompletion.enrollment_id == enrollment.id))
    known_rows = (await db.execute(known_query.group_by(BlockCharacter.character))).all()
    known = dict(known_rows)
    introduced = set(block.language_data.get("characters", []))
    for character in introduced:
        known[character] = max(known.get(character, 0), block.sequence or 0)
    return track, enrollment, block, known, introduced


def eligible_vocabulary(track, block, known, introduced, support_ids=()):
    unknown = exists(select(VocabularyCharacter.vocabulary_id).where(
        VocabularyCharacter.vocabulary_id == Vocabulary.id, VocabularyCharacter.character.not_in(known)))
    includes_current = exists(select(VocabularyCharacter.vocabulary_id).where(
        VocabularyCharacter.vocabulary_id == Vocabulary.id, VocabularyCharacter.character.in_(introduced)))
    # An explicitly taught lexical sense (e.g. 杯 as a cupful) can share a
    # character card's written form. Corrected same-glyph headings also need a
    # real Common words display; visiting that character is separate evidence.
    course_lexeme = or_(Vocabulary.language_data['course_word_id'].as_string().is_not(None),
        Vocabulary.language_data['example_context_only'].as_boolean().is_(True),
        Vocabulary.language_data['lexical_uses'].as_string().is_not(None),
        Vocabulary.id.in_(list(learning_glosses.word_meaning_corrections())))
    pinned_here = Vocabulary.language_data['introduction_block_id'].as_string() == block.id
    statement = select(Vocabulary).where(Vocabulary.language_tag == track.language_tag,
        or_(Vocabulary.text != block.text, course_lexeme), ~unknown,
        vocabulary_introduced_by(block),
        or_(includes_current, pinned_here, Vocabulary.id.in_(support_ids)))
    return statement


def vocabulary_introduced_by(block):
    # A known character does not make every later use available immediately.
    # New course senses are explicitly displayed at their own earlier card.
    boundary = Vocabulary.language_data['introduction_sequence'].as_integer()
    # Current canonical displays pin reused legacy IDs too. Earlier personal
    # exposure remains stored; it does not remove the current authoring boundary.
    return or_(boundary.is_(None), boundary <= block.sequence)


def vocabulary_display_current(item, sequence):
    """An old display stays history when its introduction moves past it."""
    boundary = item.language_data.get('introduction_sequence')
    return sequence is not None and (boundary is None or boundary <= sequence)


async def vocabulary_plan(db, track, enrollment, block, known, introduced, *, known_grammar=None):
    # Resolve prerequisites for the selected word and authored examples of its
    # related vocabulary. This is a bounded catalogue query, not a path download.
    if known_grammar is None:
        from app.services import notebook_grammar
        known_grammar = (set(item['id'] for item in notebook_grammar.catalog()['items'])
            if getattr(enrollment, 'preview', False) else await notebook_grammar.displayed(db,
                enrollment=None if hasattr(enrollment, 'completed') else enrollment,
                guest=enrollment if hasattr(enrollment, 'completed') else None))
    related = list(await db.scalars(eligible_vocabulary(track, block, known, introduced)
        .order_by(Vocabulary.rank.desc(), Vocabulary.id).limit(20)))
    scoped = list(await db.scalars(eligible_vocabulary(track, block, known, introduced).where(
        Vocabulary.language_data['lexical_uses'].as_string().is_not(None))))
    related = list({item.id: item for item in [*scoped, *related]}.values())
    scoped_support = {source for item in scoped for source in notebook_lexical_uses.support_ids(item)}
    covered_here = {source for item in scoped
        if item.language_data.get('introduction_block_id') == block.id
        for source in item.language_data.get('covered_prior_display_source_ids', [])}
    subjects = [(block.text, block.pronunciation, None)] + [
        (item.text, item.pronunciation, vocabulary_identity(item)) for item in related]
    # Only this card and a bounded set of related words contribute prerequisites.
    # Expand their lexical dependencies once to support the newly listed words'
    # own examples, without reading or querying the full sentence catalogue.
    needed = required_words(subjects, known_grammar=known_grammar)
    extra_sources = required_vocabulary_ids(subjects, known_grammar=known_grammar)
    needed.update(requirement['text'] for item in scoped for requirement in notebook_lexical_uses.requirements(item))
    unknown = exists(select(VocabularyCharacter.vocabulary_id).where(
        VocabularyCharacter.vocabulary_id == Vocabulary.id, VocabularyCharacter.character.not_in(known)))
    dependency_words = list(await db.scalars(select(Vocabulary).where(
        Vocabulary.language_tag == track.language_tag,
        or_(Vocabulary.text.in_(needed), Vocabulary.id.in_(extra_sources)),
        ~unknown, vocabulary_introduced_by(block))))
    needed.update(item.text for item in dependency_words if item.id in extra_sources)
    dependency_subjects = [(item.text, item.pronunciation, vocabulary_identity(item))
                           for item in dependency_words]
    needed.update(required_words(dependency_subjects, known_grammar=known_grammar))
    extra_sources.update(required_vocabulary_ids(dependency_subjects, known_grammar=known_grammar))
    # Resolve only the explicitly authored identities, at this actual notebook
    # boundary. A same-written-form alternate cannot stand in for the base.
    extra_words = list(await db.scalars(select(Vocabulary).where(
        Vocabulary.language_tag == track.language_tag, Vocabulary.id.in_(extra_sources),
        ~unknown, vocabulary_introduced_by(block))))
    needed.update(item.text for item in extra_words)
    core_query = select(WordBlock.text).join(LearningStage).where(
        LearningStage.track_id == track.id, WordBlock.kind == 'word', WordBlock.text.in_(needed))
    if hasattr(enrollment, "completed"):
        core_query = core_query.where(WordBlock.id.in_(enrollment.completed))
    else:
        core_query = core_query.join(WordCompletion, and_(WordCompletion.block_id == WordBlock.id, WordCompletion.enrollment_id == enrollment.id))
    core_words = set(await db.scalars(core_query))
    core_words = {word for word in core_words if set(word).issubset(known)}
    core_words.add(block.text)
    unknown = exists(select(VocabularyCharacter.vocabulary_id).where(
        VocabularyCharacter.vocabulary_id == Vocabulary.id, VocabularyCharacter.character.not_in(known)))
    lexical = {}
    lexical_ids = set()
    for item in await db.scalars(select(Vocabulary).where(Vocabulary.language_tag == track.language_tag,
        Vocabulary.text.in_(needed), ~unknown, vocabulary_introduced_by(block))
        .order_by(Vocabulary.rank.desc(), Vocabulary.id)):
        lexical.setdefault(item.text, item)
        lexical_ids.add(item.id)
    available = core_words | lexical.keys()
    primary_ids, support_ids = set(), set(scoped_support)
    for index, (text, pronunciation, identity) in enumerate(subjects):
        for entry in preferred_entries(text, pronunciation, available, limit=3 if index == 0 else 1,
                                       vocabulary=identity, available_vocabulary_ids=lexical_ids,
                                       known_grammar=known_grammar):
            dependencies = {
                token.get("vocabularyId") or lexical[token["text"]].id
                for token in entry["tokens"] if is_word(token["text"]) and
                (token.get("vocabularyId") or token["text"] in lexical and token["text"] not in core_words)
            }
            dependencies.update(entry.get('requiresVocabularyIds', []))
            support_ids.update(dependencies)
            if index == 0:
                primary_ids.update(dependencies)
    # Priority depends on content and character progress, never exposure history;
    # acknowledgement between pages cannot reorder cursors or hide unseen words.
    # Complete scoped teaching and its exact supporting word rows travel together
    # before ordinary related entries. Legacy records remain stored; only an
    # explicitly reviewed covered display is suppressed at this exact card.
    priority = case((Vocabulary.id.in_(scoped_support), -3),
                    (Vocabulary.id.in_({item.id for item in scoped}), -2),
                    (Vocabulary.language_data['introduction_block_id'].as_string() == block.id, -1),
                    (Vocabulary.id.in_(primary_ids), 0), (Vocabulary.id.in_(support_ids), 1), else_=2)
    statement = eligible_vocabulary(track, block, known, introduced, support_ids)
    if covered_here:
        statement = statement.where(Vocabulary.id.not_in(covered_here))
    return statement, priority, core_words, needed, primary_ids


async def common_vocabulary(db, user_id, track_id, block_id, *, after=None, limit=20, guest=None):
    track, enrollment, block, known, introduced = await vocabulary_context(db, user_id, track_id, block_id, guest=guest)
    if block.kind != "word" or block.language_data.get("kind") != "mandarin-simplified":
        return {"items": [], "nextCursor": None, "totalCount": 0}
    from app.services import notebook_grammar
    known_grammar = (set(item['id'] for item in notebook_grammar.catalog()['items'])
        if guest is not None and guest.preview else await notebook_grammar.displayed(db,
            enrollment=enrollment if guest is None else None, guest=guest))
    statement, priority, core_words, needed, primary_ids = await vocabulary_plan(db,
        track, enrollment, block, known, introduced, known_grammar=known_grammar)
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    if after:
        cursor = (await db.execute(statement.add_columns(priority).where(Vocabulary.id == after))).first()
        if cursor is None:
            raise learning_error(422, "learning_invalid_cursor")
        word, cursor_priority = cursor
        statement = statement.where(or_(priority > cursor_priority, and_(priority == cursor_priority,
            or_(Vocabulary.rank < word.rank, and_(Vocabulary.rank == word.rank, Vocabulary.id > word.id)))))
    items = list(await db.scalars(statement.order_by(priority, Vocabulary.rank.desc(), Vocabulary.id).limit(limit + 1)))
    more = len(items) > limit
    items = items[:limit]
    # New prerequisites must be visibly listed on this page before use. A GET
    # never records exposure, and unrequested pages never count as introductions.
    seen_query = select(Vocabulary).where(Vocabulary.text.in_(needed | {item.text for item in items}))
    if guest is not None:
        seen_query = seen_query.where(Vocabulary.id.in_(guest.seen))
    else:
        seen_query = seen_query.join(VocabularyExposure, VocabularyExposure.vocabulary_id == Vocabulary.id).where(VocabularyExposure.enrollment_id == enrollment.id)
    seen_rows = list(await db.scalars(seen_query))
    seen_ids = {row.id for row in seen_rows}
    known_words = core_words | {row.text for row in seen_rows if set(row.text).issubset(known)}
    known_words.update(item.text for item in items)
    known_vocabulary_ids = seen_ids | {item.id for item in items}
    lexical_refs = [SimpleNamespace(**notebook_lexical_uses.reference(item)) for item in items
        if notebook_lexical_uses.requires_display(item)
        and notebook_lexical_uses.support_ids(item) <= known_vocabulary_ids]
    # Ordinary words can also move past an old personal display. Apply the
    # shared current-position and dependency closure on every page; scoped
    # teaching additionally requires its exact fingerprinted receipt.
    _, current_sources = await notebook_grammar.lexical_evidence(db,
        enrollment=enrollment if guest is None else None, guest=guest,
        block=block, visible_ids=[item.id for item in items], lexical_refs=lexical_refs)
    known_vocabulary_ids.intersection_update(current_sources)
    seen_ids.intersection_update(current_sources)
    known_words = core_words | {item.text for item in [*seen_rows, *items]
        if item.id in known_vocabulary_ids and set(item.text).issubset(known)}
    examples = sentence_examples(block.text, block.pronunciation, known_words, introduced,
        known_vocabulary_ids=known_vocabulary_ids, known_grammar=known_grammar)
    grammar_notes = [] if after else await notebook_grammar.notes(db, block,
        enrollment=enrollment if guest is None else None, guest=guest, visible_ids=[item.id for item in items],
        lexical_refs=lexical_refs)
    return {"example": examples[0] if examples else None, "examples": examples,
            "grammarNotes": [notebook_grammar.public_note(item) for item in grammar_notes],
            "items": [{"id": item.id, "text": item.text, "translations": vocabulary_translations(item),
                     'lexicalUses': notebook_lexical_uses.public_uses(item),
                     'lexicalUseReference': notebook_lexical_uses.reference(item),
                     'characterContexts': character_contexts_for_word(item.id, item.text, item.pronunciation),
                     "pronunciation": audio_pronunciation(item.text, item.pronunciation), "characters": item.language_data["characters"],
                     "example": example_sentence(item.text, item.pronunciation, known_words,
                         introduced if introduced.intersection(item.language_data['characters']) else set(item.language_data['characters']),
                         vocabulary=vocabulary_identity(item), known_vocabulary_ids=known_vocabulary_ids,
                         known_grammar=known_grammar),
                     "supportsExample": item.id in primary_ids and not introduced.intersection(item.language_data['characters']),
                     "newlyAvailable": item.language_data.get('introduction_block_id') == block.id or
                         (item.id in primary_ids and item.id not in seen_ids) or max(known.get(char, 0) for char in item.language_data["characters"]) == block.sequence}
                    for item in items], "nextCursor": items[-1].id if more else None, "totalCount": total}


def vocabulary_translations(item):
    # Generic surface-form glosses must not overwrite a separately introduced
    # sense such as simultaneous 一边 with its different "one side" meaning.
    if item.sense_key:
        return item.translations
    return word_translations(item.translations, vocabulary_id=item.id, text=item.text,
                             pronunciation=item.pronunciation, sense_key=item.sense_key)


async def store_vocabulary_exposures(db, enrollment_id, exposures):
    exposures = list(exposures)
    if not exposures:
        return
    boundaries = dict((await db.execute(select(Vocabulary.id,
        Vocabulary.language_data['introduction_sequence'].as_integer()).where(
            Vocabulary.id.in_({word for word, _, _ in exposures})))).all())
    # Prefer the earliest valid CURRENT display before aggregating an import.
    # Otherwise an old100 can hide a real200 when the source now starts150.
    # With no current candidate, retain the earliest legacy aggregate as history;
    # every current consumer filters it and a genuine redisplay can repair it.
    earliest = {}
    current = {}
    for vocabulary_id, block_id, sequence in exposures:
        if sequence is None or vocabulary_id not in boundaries:
            continue
        boundary = boundaries[vocabulary_id]
        valid = boundary is None or boundary <= sequence
        previous = earliest.get(vocabulary_id)
        if (previous is None or valid and not current[vocabulary_id]
                or valid == current[vocabulary_id] and sequence < previous[1]):
            earliest[vocabulary_id] = (block_id, sequence)
            current[vocabulary_id] = valid
    items = sorted(earliest.items())
    for start in range(0, len(items), 1000):
        statement = insert(VocabularyExposure).values([
            {'enrollment_id': enrollment_id, 'vocabulary_id': word, 'block_id': block[0]}
            for word, block in items[start:start + 1000]])
        # PostgreSQL exposes these two rows in ON CONFLICT. Keep the fixed
        # references correlated instead of adding either table to the subquery.
        incoming_sequence = select(WordBlock.sequence).where(
            WordBlock.id == literal_column('excluded.block_id')).scalar_subquery()
        existing_sequence = select(WordBlock.sequence).where(
            WordBlock.id == literal_column('learning_vocabulary_exposures.block_id')).scalar_subquery()
        current_boundary = select(Vocabulary.language_data['introduction_sequence'].as_integer()).where(
            Vocabulary.id == literal_column('excluded.vocabulary_id')).scalar_subquery()
        await db.execute(statement.on_conflict_do_update(
            index_elements=['enrollment_id', 'vocabulary_id'],
            set_={'block_id': statement.excluded.block_id},
            # Keep the earliest valid display. A reorder can leave the old
            # aggregate before the current introduction; only a real display
            # at/after that introduction can repair it. Visits and separately
            # fingerprinted lexical/grammar history are never rewritten.
            where=and_(or_(current_boundary.is_(None), current_boundary <= incoming_sequence),
                or_(incoming_sequence < existing_sequence, existing_sequence < current_boundary))))


async def record_vocabulary_seen(db, user_id, track_id, block_id, vocabulary_ids, grammar=(), lexical_uses=()):
    track, enrollment, block, known, introduced = await vocabulary_context(db, user_id, track_id, block_id)
    if block.kind != 'word' or block.language_data.get('kind') != 'mandarin-simplified':
        raise learning_error(422, 'learning_invalid_vocabulary')
    ids = set(vocabulary_ids)
    statement, _, _, _, _ = await vocabulary_plan(db, track, enrollment, block, known, introduced)
    eligible = set(await db.scalars(statement
        .with_only_columns(Vocabulary.id).where(Vocabulary.id.in_(ids))))
    if eligible != ids:
        raise learning_error(422, 'learning_invalid_vocabulary')
    from app.services import notebook_grammar, notebook_lexical_progress
    words = {word.id: word for word in await db.scalars(select(Vocabulary).where(Vocabulary.id.in_(ids)))}
    claimed = {(ref.id, ref.fingerprint) for ref in lexical_uses}
    if any(ref.id not in words for ref in lexical_uses):
        raise learning_error(422, 'learning_invalid_vocabulary')
    expected = {word.id: notebook_lexical_uses.reference(word) for word in words.values()
                if notebook_lexical_uses.requires_display(word)}
    if claimed != {(ref['id'], ref['fingerprint']) for ref in expected.values()}:
        raise learning_error(409, 'learning_lexical_scope_changed')
    if expected:
        _, displayed_sources = await notebook_grammar.lexical_evidence(db, enrollment=enrollment,
            block=block, visible_ids=ids, lexical_refs=lexical_uses)
        if not set(expected) <= displayed_sources.keys():
            raise learning_error(422, 'learning_invalid_vocabulary')
    current_notes = {item['id']: item for item in notebook_grammar.catalog()['items']}
    if any(ref.id in current_notes and ref.fingerprint != current_notes[ref.id]['fingerprint'] for ref in grammar):
        raise learning_error(409, 'learning_grammar_changed')
    notes = await notebook_grammar.notes(db, block, enrollment=enrollment,
        visible_ids=ids, lexical_refs=lexical_uses) if grammar else []
    wanted = {(item.id, item.fingerprint) for item in grammar}
    accepted = [item for item in notes if (item['id'], item['fingerprint']) in wanted]
    if len(accepted) != len(wanted):
        raise learning_error(422, 'learning_invalid_grammar')
    known_grammar = await notebook_grammar.displayed(db, enrollment=enrollment) if accepted else set()
    for item in accepted:
        if not set(item['requires_grammar']) <= known_grammar:
            raise learning_error(422, 'learning_invalid_grammar')
        known_grammar.add(item['id'])
    await store_vocabulary_exposures(db, enrollment.id,
                                    [(word, block_id, block.sequence) for word in ids])
    await notebook_lexical_progress.store(db, enrollment.id, block, lexical_uses)
    await notebook_grammar.store_displays(db, enrollment.id, accepted)
    await db.commit()
    return {'ok': True}
