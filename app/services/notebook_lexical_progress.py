"""Personal display receipts for complete, source-bound lexical-use teaching."""
from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.models.learning import LexicalUseExposure, Vocabulary, VocabularyExposure, WordBlock
from app.services import notebook_lexical_uses as uses


async def receipts(db, *, enrollment=None, guest=None, block=None, visible_refs=()):
    if guest is not None:
        result = [(block_id, ref.id, ref.fingerprint)
                  for block_id, refs in guest.lexical_use_exposures.items() for ref in refs]
    elif enrollment is not None:
        result = list((await db.execute(select(LexicalUseExposure.block_id,
            LexicalUseExposure.vocabulary_id, LexicalUseExposure.fingerprint)
            .where(LexicalUseExposure.enrollment_id == enrollment.id))).all())
    else:
        result = []
    if block is not None:
        result.extend((block.id, ref.id, ref.fingerprint) for ref in visible_refs)
    return result


def closed_sources(sources, blocks, displays):
    """Keep old source history; only current complete scopes become evidence.

    A changed fingerprint does not erase history or establish the replacement.
    Supporting source readings must actually be displayed by the scope's own
    display position, rather than merely known by a later exercise anchor.
    """
    from app.services.course_eligibility import matches
    accepted = {key: value for key, value in sources.items() if not uses.requires_display(value[0])}
    pending = {key: value for key, value in sources.items() if uses.requires_display(value[0])}
    positions = {}
    for block_id, source_id, fingerprint in displays:
        source = pending.get(source_id)
        block = blocks.get(block_id)
        if source is None or block is None or block.sequence is None:
            continue
        ref = uses.reference(source[0])
        if ref['fingerprint'] != fingerprint or block.sequence < source[1]:
            continue
        positions.setdefault(source_id, set()).add(block.sequence)
    while pending:
        added = {}
        for source_id, (word, _) in pending.items():
            def ready(requirement, sequence):
                if requirement.get('source_id') == source_id:
                    return matches(requirement, word)
                if any(block_id in blocks and blocks[block_id].sequence is not None
                       and blocks[block_id].sequence <= sequence and matches(requirement, blocks[block_id])
                       for block_id in requirement.get('primary_block_ids', [])):
                    return True
                source = accepted.get(requirement.get('source_id'))
                return source is not None and source[1] <= sequence and matches(requirement, source[0])
            valid = [sequence for sequence in positions.get(source_id, ())
                     if all(ready(requirement, sequence) for requirement in uses.requirements(word))]
            if valid:
                added[source_id] = (word, min(valid))
        if not added:
            break
        accepted.update(added)
        for source_id in added:
            del pending[source_id]
    return accepted


async def close(db, sources, blocks, **context):
    if not any(uses.requires_display(word) for word, _ in sources.values()):
        return sources
    return closed_sources(sources, blocks, await receipts(db, **context))


async def store(db, enrollment_id, block, refs):
    values = {(ref.id, ref.fingerprint) for ref in refs}
    if not values:
        return
    statement = insert(LexicalUseExposure).values([
        {'enrollment_id': enrollment_id, 'vocabulary_id': source_id,
         'fingerprint': fingerprint, 'block_id': block.id}
        for source_id, fingerprint in sorted(values)])
    incoming = select(WordBlock.sequence).where(
        WordBlock.id == literal_column('excluded.block_id')).scalar_subquery()
    existing = select(WordBlock.sequence).where(
        WordBlock.id == literal_column('learning_lexical_use_exposures.block_id')).scalar_subquery()
    introduction = select(Vocabulary.language_data['introduction_sequence'].as_integer()).where(
        Vocabulary.id == literal_column('excluded.vocabulary_id')).scalar_subquery()
    displayed = select(WordBlock.sequence).join(VocabularyExposure,
        VocabularyExposure.block_id == WordBlock.id).where(
            VocabularyExposure.enrollment_id == enrollment_id,
            VocabularyExposure.vocabulary_id == literal_column('excluded.vocabulary_id')).scalar_subquery()
    boundary = func.greatest(introduction, displayed)
    # Keep the earliest scope display that can close CURRENT source evidence.
    # A reorder or a repaired aggregate may leave the same fingerprint at an
    # obsolete position. Only a real current redisplay repairs that position;
    # other historical scope fingerprints remain untouched.
    await db.execute(statement.on_conflict_do_update(
        index_elements=['enrollment_id', 'vocabulary_id', 'fingerprint'],
        set_={'block_id': statement.excluded.block_id},
        where=and_(or_(boundary.is_(None), boundary <= incoming),
                   or_(incoming < existing, existing < boundary))))
