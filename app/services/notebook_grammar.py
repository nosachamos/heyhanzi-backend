"""Authored notebook explanations and exact display evidence, never mastery."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.learning import (
    GrammarExposure,
    Vocabulary,
    VocabularyExposure,
    WordBlock,
    WordCompletion,
)
from app.services import learning
from app.services.course_eligibility import matches

CONTENT = Path(__file__).resolve().parents[1] / 'data/learning/notebook-grammar.json'


@lru_cache(maxsize=1)
def _read(path, modified, size):
    data = json.loads(Path(path).read_text())
    if data.get('format_version') != 1 or data.get('track_id') != 'mandarin-simplified-v1':
        raise ValueError('Unsupported notebook grammar catalog')
    ids = set()
    last_sequence = 0
    for item in data['items']:
        digest = hashlib.sha256(json.dumps({k: v for k, v in item.items() if k != 'fingerprint'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if item['id'] in ids or item['fingerprint'] != digest:
            raise ValueError('Duplicate or stale notebook grammar explanation')
        if item['block_sequence'] < last_sequence or not set(item['requires_grammar']) <= ids:
            raise ValueError('Notebook grammar explanations are not in prerequisite order')
        ids.add(item['id'])
        last_sequence = item['block_sequence']
    return data


def catalog():
    # Existing notebook clients remain usable before an authorized teaching
    # export exists. No missing record can satisfy a story grammar requirement.
    if not CONTENT.is_file():
        return {'items': [], 'story_requirements': {}}
    stat = CONTENT.stat()
    return _read(str(CONTENT), stat.st_mtime_ns, stat.st_size)


def reference(item):
    return {'id': item['id'], 'fingerprint': item['fingerprint']}


def public_note(item):
    # Media can be requalified without invalidating the unchanged explanation
    # the learner actually saw. Only authored note fields enter its fingerprint.
    audio = catalog().get('audio', {})
    return {key: item[key] for key in ['id', 'fingerprint', 'title', 'explanation']} | {
        'examples': [example | ({'audio': audio[example['audio_key']]}
                     if example.get('audio_key') in audio else {}) for example in item['examples']]}


def dependency_closed(items):
    accepted = set()
    while True:
        added = {item['id'] for item in items if set(item['requires_grammar']) <= accepted}
        if added <= accepted:
            return accepted
        accepted.update(added)


async def lexical_evidence(db, *, enrollment=None, guest=None, block=None, visible_ids=(), lexical_refs=()):
    if guest is not None:
        ids = set(guest.completed)
        if block is not None:
            ids.add(block.id)
        blocks = {item.id: item for item in await db.scalars(select(WordBlock).where(WordBlock.id.in_(ids)))}
        displays = [(source_id, blocks[block_id].sequence) for block_id, source_ids in guest.exposures.items()
                    if block_id in blocks for source_id in source_ids]
    else:
        blocks = {item.id: item for item in await db.scalars(select(WordBlock).join(WordCompletion).where(
            WordCompletion.enrollment_id == enrollment.id))}
        if block is not None:
            blocks[block.id] = block
        displays = (await db.execute(select(VocabularyExposure.vocabulary_id, WordBlock.sequence)
            .join(WordBlock, WordBlock.id == VocabularyExposure.block_id)
            .where(VocabularyExposure.enrollment_id == enrollment.id))).all()
    current = [(source_id, block.sequence) for source_id in visible_ids] if block else []
    positions = [*displays, *current]
    ids = {source_id for source_id, _ in positions}
    words = {item.id: item for item in await db.scalars(select(Vocabulary).where(Vocabulary.id.in_(ids)))} if ids else {}
    first = {}
    for source_id, sequence in positions:
        word = words.get(source_id)
        if word is None:
            if guest is not None:
                raise learning.learning_error(422, 'learning_invalid_vocabulary')
            continue
        # Preserve obsolete display positions in account/browser history, but
        # select the earliest CURRENT position. Taking min() first makes one
        # old display permanently mask a later legitimate redisplay.
        if learning.vocabulary_display_current(word, sequence):
            first[source_id] = min(first.get(source_id, sequence), sequence)
    sources = {}
    for source_id, sequence in first.items():
        sources[source_id] = (words[source_id], sequence)
    from app.services import notebook_lexical_progress
    sources = await notebook_lexical_progress.close(db, sources, blocks,
        enrollment=enrollment, guest=guest, block=block, visible_refs=lexical_refs)
    return blocks, sources


def lexically_ready(item, evidence):
    blocks, sources = evidence
    for word in item['lexical_requirements']:
        if any(block_id in blocks and blocks[block_id].sequence is not None
               and blocks[block_id].sequence <= item['block_sequence'] and matches(word, blocks[block_id])
               for block_id in word.get('primary_block_ids', [])):
            continue
        source = sources.get(word.get('source_id'))
        if source is None or source[1] > item['block_sequence'] or not matches(word, source[0]):
            return False
    return True


async def guest_evidence(db, guest):
    if not guest.grammar_exposures:
        return []
    by_id = {item['id']: item for item in catalog()['items']}
    next_word = await learning.next_word_for(db, guest)
    permitted = guest.completed | ({next_word.id} if next_word else set())
    evidence = await lexical_evidence(db, guest=guest, block=next_word)
    selected = {}
    historical = set()
    for block_id, refs in guest.grammar_exposures.items():
        current_position = block_id in permitted
        if not current_position and block_id not in guest.historical_pending:
            raise learning.learning_error(422, 'learning_invalid_grammar')
        for ref in refs:
            item = by_id.get(ref.id)
            if item is None:
                raise learning.learning_error(422, 'learning_invalid_grammar')
            historical.add(item['id'])
            if not current_position:
                continue  # Retain the old prerequisite receipt, never current teaching.
            if ref.fingerprint != item['fingerprint']:
                continue  # Retain stale local history, never grant current exposure.
            if item['block_id'] != block_id:
                raise learning.learning_error(422, 'learning_invalid_grammar')
            if not lexically_ready(item, evidence):
                continue  # Retain history; a revised lexical scope must be reread.
            selected[item['id']] = item
    if any(not set(item['requires_grammar']) <= historical for item in selected.values()):
        raise learning.learning_error(422, 'learning_invalid_grammar')
    # A legitimate prerequisite correction invalidates dependent evidence until
    # it is reread; it must not make the guest's whole history unrecoverable.
    current = dependency_closed(selected.values())
    return [item for id, item in selected.items() if id in current]


async def displayed(db, *, enrollment=None, guest=None):
    if guest is not None:
        return {item['id'] for item in await guest_evidence(db, guest)}
    if enrollment is None:
        return set()
    rows = await db.scalars(select(GrammarExposure).where(GrammarExposure.enrollment_id == enrollment.id))
    by_id = {item['id']: item for item in catalog()['items']}
    current = [by_id[row.grammar_id] for row in rows if row.grammar_id in by_id
               and by_id[row.grammar_id]['fingerprint'] == row.fingerprint
               and by_id[row.grammar_id]['block_id'] == row.block_id]
    if current:
        next_word = await learning.next_word_for(db, enrollment)
        evidence = await lexical_evidence(db, enrollment=enrollment, block=next_word)
        current = [item for item in current if lexically_ready(item, evidence)]
    return dependency_closed(current)


async def notes(db, block, *, enrollment=None, guest=None, visible_ids=(), lexical_refs=()):
    candidates = [item for item in catalog()['items'] if item['block_id'] == block.id]
    if not candidates:
        return []
    if guest is not None and guest.preview:
        return candidates
    evidence = await lexical_evidence(db, enrollment=enrollment, guest=guest, block=block,
        visible_ids=visible_ids, lexical_refs=lexical_refs)
    known = await displayed(db, enrollment=enrollment, guest=guest)
    shown = []
    for item in candidates:
        if item['block_sequence'] != block.sequence:
            raise ValueError('Notebook grammar anchor moved')
        if set(item['requires_grammar']) <= known and lexically_ready(item, evidence):
            shown.append(item)
            known.add(item['id'])
    return shown


async def store_displays(db, enrollment_id, items):
    if items:
        await db.execute(insert(GrammarExposure).values([
            {'enrollment_id': enrollment_id, 'grammar_id': item['id'],
             'fingerprint': item['fingerprint'], 'block_id': item['block_id']}
            for item in items]).on_conflict_do_nothing())


def story_requirements(item):
    # Shared by every course task, including optional sentence practice. A word
    # display never substitutes for a construction's actual introduction.
    document = catalog()
    mapped = document.get('exercise_requirements', {}).get(item['id'])
    if mapped is None:
        mapped = document.get('cloze_requirements', {}).get(item['id'])
    if mapped is None:
        mapped = document.get('story_requirements', {}).get(item['id'])
    return set(mapped if mapped is not None else item['preparation']['grammar']['ids'])
