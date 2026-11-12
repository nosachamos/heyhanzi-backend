"""Current source-bound display history for public learner API scenarios."""

import copy
import json
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]


def current_introductions(config, rows):
    """Retain lexical identities while using their migrated teaching anchors."""
    with psycopg2.connect(config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,language_data FROM learning_vocabulary WHERE id=ANY(%s)',
                           ([row['id'] for row in rows],))
            metadata = dict(cursor.fetchall())
            cursor.execute('SELECT id,sequence FROM learning_word_blocks')
            positions = dict(cursor.fetchall())
    return [{**row, 'introduction_block_id': metadata[row['id']]['introduction_block_id'],
             'introduction_sequence': positions[metadata[row['id']]['introduction_block_id']]}
            for row in rows]


def complete_display_history(config, notebook, instance_id):
    """Represent prior displays using the actual migrated source records.

    This is fixture history, not task completion. The test still sends it through
    the real public eligibility/import/grading APIs and their current guards.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.models.learning import Vocabulary
    from app.services import notebook_lexical_uses
    from app.services.course_content import exercise

    result = copy.deepcopy(notebook)
    for key in ['exposures', 'grammarExposures', 'lexicalUseExposures']:
        result.setdefault(key, {})
    notes = json.loads((ROOT / 'app/data/learning/notebook-grammar.json').read_text())
    requirements = json.loads((ROOT / 'app/data/learning/course-exercises.json').read_text())['learner_policy']['lexical_requirements'][instance_id]
    by_id = {note['id']: note for note in notes['items']}
    needed = set(notes.get('exercise_requirements', {}).get(instance_id,
        notes.get('cloze_requirements', {}).get(instance_id,
        notes.get('story_requirements', {}).get(instance_id, exercise(instance_id)['preparation']['grammar']['ids']))))
    for note in reversed(notes['items']):
        if note['id'] in needed:
            needed.update(note['requires_grammar'])
    for identifier in needed:
        if identifier in by_id:
            note = by_id[identifier]
            assert note['block_id'] in result['completed'], identifier
            result['grammarExposures'].setdefault(note['block_id'], []).append(
                {key: note[key] for key in ['id', 'fingerprint']})
            requirements.extend(note['lexical_requirements'])
    completed = set(result['completed'])
    pending = {row['source_id'] for row in requirements if row.get('source_id')
               and not completed.intersection(row.get('primary_block_ids', []))}
    loaded = {}
    engine = create_engine(config['DATABASE']['Url'])
    try:
        with Session(engine) as session:
            while pending - loaded.keys():
                rows = session.scalars(select(Vocabulary).where(Vocabulary.id.in_(pending - loaded.keys()))).all()
                assert rows, 'Missing persisted vocabulary in historical fixture'
                for word in rows:
                    loaded[word.id] = word
                    pending.update(row['source_id'] for row in notebook_lexical_uses.requirements(word)
                        if row.get('source_id') and not completed.intersection(row.get('primary_block_ids', [])))
            for word in loaded.values():
                block = word.language_data['introduction_block_id']
                assert block in completed, word.id
                result['exposures'].setdefault(block, []).append(word.id)
                reference = notebook_lexical_uses.reference(word)
                if reference:
                    result['lexicalUseExposures'].setdefault(block, []).append(reference)
    finally:
        engine.dispose()
    return result
