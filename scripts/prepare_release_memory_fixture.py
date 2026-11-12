"""Seed only the isolated release-memory rehearsal's disposable database.

No production account, database, resource or provider is modified. The fixture
represents an existing learner's notebook displays; HTTP requests still exercise
real authentication, eligibility, course content and serialization.
"""
import asyncio
import hashlib
import json
import os
import resource
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config_loader import env  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.models.auth import User  # noqa: E402
from app.models.learning import (  # noqa: E402
    GrammarExposure,
    LearningEnrollment,
    LearningStage,
    LexicalUseExposure,
    Vocabulary,
    VocabularyExposure,
    WordBlock,
    WordCompletion,
)
from app.security.passwords import hash_password  # noqa: E402
from app.services import course_content, notebook_grammar, notebook_lexical_uses  # noqa: E402
from app.utils.time import utc_now_seconds  # noqa: E402


async def main():
    config = env.config
    address = make_url(config['DATABASE']['Url'])
    password = os.environ.get('HEYHANZI_MEMORY_FIXTURE_PASSWORD', '')
    if (address.host != 'db' or address.database != 'heyhanzi_test'
            or config['GENERAL'].get('ReleaseSha') != 'memory-check' or len(password) < 20):
        raise ValueError('Memory fixture requires the isolated disposable rehearsal database')
    data, items = course_content.catalog()
    cloze = course_content.exercise(next(item['id'] for item in items.values() if item['definition']['id'] == 'ACT-CLOZE'))
    stories = [item for item in items.values() if item['definition']['id'] in {'ACT-STORY', 'ACT-STORY-COMP'}
               and course_content.is_optional(item)]
    def body_size(item):
        reference = item.get('definition_ref')
        return ((course_content.CONTENT.parent / reference['path']).stat().st_size
                if reference else len(json.dumps(item['definition'], ensure_ascii=False)))

    story = course_content.exercise(max(stories, key=body_size)['id'])
    targets = [cloze, story]
    # Exercise the real mixed whole/sentence delivery example as well as the
    # largest payload. This fixture never rewrites teaching or media catalogs.
    sentence_story = course_content.exercise('EX-STORY-U3-06') if 'EX-STORY-U3-06' in items else None
    if sentence_story is not None and sentence_story not in targets:
        targets.append(sentence_story)
    positions = {item['id']: position for position, item in enumerate(data['exercises'])}
    required_target_positions = [positions[item['id']] for item in targets
                                 if not course_content.is_optional(item)]
    prefix = data['exercises'][:max(required_target_positions, default=0)]
    prerequisites = [item for item in prefix if not course_content.is_optional(item)]
    teaching = targets + prerequisites
    policy = data['learner_policy']
    requirements = [word for item in teaching for word in policy['lexical_requirements'][item['id']]]
    grammar = {item['id']: item for item in notebook_grammar.catalog()['items']}
    needed = set().union(*(notebook_grammar.story_requirements(item) for item in teaching))
    while True:
        expanded = needed | {value for key in needed for value in grammar[key]['requires_grammar']}
        if expanded == needed:
            break
        needed = expanded
    for key in needed:
        requirements.extend(grammar[key]['lexical_requirements'])
    exposures = {}
    for word in requirements:
        if not word['primary_block_ids'] and word.get('source_id'):
            exposures.setdefault(word['source_id'], word['introduction_block_id'])
    sequence = max([policy['trail_placements'][item['id']]['afterSequence'] for item in targets]
                   + [grammar[key]['block_sequence'] for key in needed])
    database = Database(config)
    lexical_displays = defaultdict(list)
    try:
        async with database.SessionLocal() as db:
            # The rehearsal represents current teaching displays, including the
            # exact lexical-scope receipts now required by real eligibility.
            pending = set(exposures)
            scoped = {}
            while pending:
                words = list(await db.scalars(select(Vocabulary).where(Vocabulary.id.in_(pending))))
                if {word.id for word in words} != pending:
                    raise ValueError('Rehearsal is missing an authored vocabulary source')
                pending = set()
                for word in words:
                    scoped[word.id] = word
                    if not exposures[word.id]:
                        block = word.language_data.get('introduction_block_id')
                        introduced = word.language_data.get('introduction_sequence', sequence + 1)
                        if not block or introduced > sequence:
                            raise ValueError('Rehearsal lacks a prior lexical display for ' + word.id)
                        exposures[word.id] = block
                    for requirement in notebook_lexical_uses.requirements(word):
                        source = requirement.get('source_id')
                        if requirement.get('primary_block_ids') or not source or source in exposures:
                            continue
                        block = requirement.get('introduction_block_id')
                        if block and requirement.get('introduction_block_sequence', sequence + 1) > sequence:
                            raise ValueError('Rehearsal lexical support exceeds its notebook prefix: ' + source)
                        exposures[source] = block
                        pending.add(source)
            blocks = list(await db.scalars(select(WordBlock).join(LearningStage).where(
                LearningStage.track_id == course_content.TRACK_ID,
                WordBlock.kind == 'word', WordBlock.sequence <= sequence).order_by(WordBlock.sequence)))
            stamp = utc_now_seconds()
            user = User(name='Memory rehearsal', email_normalized='memory-learner@example.com',
                email_verified_at=stamp, email_verification_source='memory-rehearsal',
                password_hash=hash_password(password).encoded_hash, password_updated_at=stamp,
                new_device_alert_at=stamp)
            db.add(user)
            await db.flush()
            enrollment = LearningEnrollment(user_id=user.id, track_id=course_content.TRACK_ID,
                pinyin_completed=['welcome', 'sounds', 'tones', 'spelling'])
            db.add(enrollment)
            await db.flush()
            db.add_all(WordCompletion(enrollment_id=enrollment.id, block_id=block.id) for block in blocks)
            db.add_all(VocabularyExposure(enrollment_id=enrollment.id, vocabulary_id=source, block_id=block)
                       for source, block in exposures.items())
            for source, word in scoped.items():
                reference = notebook_lexical_uses.reference(word)
                if reference:
                    block = exposures[source]
                    db.add(LexicalUseExposure(enrollment_id=enrollment.id, vocabulary_id=source,
                        fingerprint=reference['fingerprint'], block_id=block))
                    lexical_displays[block].append(reference)
            db.add_all(GrammarExposure(enrollment_id=enrollment.id, grammar_id=key,
                fingerprint=grammar[key]['fingerprint'], block_id=grammar[key]['block_id']) for key in needed)
            await db.commit()
    finally:
        await database.dispose()
    word_displays = defaultdict(list)
    for source, block in exposures.items():
        word_displays[block].append(source)
    grammar_displays = defaultdict(list)
    for key in sorted(needed):
        note = grammar[key]
        grammar_displays[note['block_id']].append({'id': key, 'fingerprint': note['fingerprint']})
    raw = course_content.CONTENT.read_bytes()
    print(json.dumps({'email': user.email_normalized,
        'requiredPrefix': [item['id'] for item in prerequisites],
        'targets': [{'id': item['id'], 'fingerprint': item['fingerprint'],
                     'paragraphDelivery': {
                         pid: {voice: {'kind': 'clips', 'count': len(value['clips'])}
                               if 'clips' in value else {'kind': 'whole'}
                               for voice, value in variants.items()}
                         for pid, variants in item['definition'].get('storyActivity', {})
                         .get('recordings', {}).get('paragraphs', {}).items()}}
                    for item in targets],
        'notebook': {'trackId': course_content.TRACK_ID, 'completed': [block.id for block in blocks],
                     'pinyinCompleted': ['welcome', 'sounds', 'tones', 'spelling'],
                     'exposures': word_displays, 'grammarExposures': grammar_displays,
                     'lexicalUseExposures': lexical_displays},
        'catalogSha256': hashlib.sha256(raw).hexdigest(), 'catalogBytes': len(raw),
        'exerciseCount': len(items), 'fixturePeakRssKiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))


if __name__ == '__main__':
    asyncio.run(main())
