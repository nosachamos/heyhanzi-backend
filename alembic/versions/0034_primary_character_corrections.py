"""Correct first-taught readings and require actual exposure to the corrected sense.

Existing character visits, vocabulary exposures and exercise evidence remain intact.
The additional lexical IDs identify replacement teaching, not new mastery targets.
"""
import json
import re
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op

revision = '0034_primary_character_corrections'
down_revision = '0033_success_feedback_settings'
branch_labels = None
depends_on = None


def upgrade():
    # Keep the stable descriptive revision IDs; Alembic's default is 32.
    op.alter_column('alembic_version', 'version_num', existing_type=sa.String(32),
                    type_=sa.String(128), existing_nullable=False)
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_word_blocks', 'learning_vocabulary', 'learning_vocabulary_characters'])
    blocks = metadata.tables['learning_word_blocks']
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    directory = Path(__file__).resolve().parents[1] / 'data'
    document = json.loads((directory / '0034_primary_character_corrections.json').read_text())
    for item in document['corrections']:
        block = connection.execute(sa.select(blocks).where(blocks.c.id == item['blockId'])).mappings().one()
        if (block['kind'] != 'word' or block['text'] != item['text'] or block['sequence'] != item['sequence']
                or block['translations'] not in [item['previous']['translations'], item['translations']]
                or block['pronunciation'] not in [item['previous']['pronunciation'], item['pronunciation']]):
            raise RuntimeError('Pinned primary character no longer matches: ' + item['blockId'])
        previous = {'revision': revision, **item['previous']}
        data = dict(block['language_data'])
        if data.get('previous_primary_teaching', previous) != previous:
            raise RuntimeError('Conflicting primary correction provenance: ' + item['blockId'])
        connection.execute(blocks.update().where(blocks.c.id == item['blockId']).values(
            translations=item['translations'], pronunciation=item['pronunciation'],
            language_data={**data, 'pinyin': item['pronunciation']['value'],
                'previous_primary_teaching': previous, 'primary_vocabulary_id': item['sourceId']}))
        record = dict(id=item['sourceId'], language_tag='cmn-Hans', text=item['text'],
            translations=item['translations'], pronunciation=item['pronunciation'],
            level=f'hsk-{item["level"]}', rank=1000000000,
            reading_key=re.sub(r"[\s'’]+", '', item['pronunciation']['value'].lower()),
            sense_key='primary-correction-v1', language_data={
                'kind': 'mandarin-simplified', 'characters': [item['text']],
                'course_word_id': item['courseWordId'], 'primary_correction_revision': revision,
                'introduction_block_id': item['blockId'], 'introduction_sequence': item['sequence']})
        current = connection.execute(sa.select(vocabulary).where(vocabulary.c.id == item['sourceId'])).mappings().one_or_none()
        if current is None:
            connection.execute(vocabulary.insert().values(**record))
        elif any(current[key] != value for key, value in record.items()):
            raise RuntimeError('Conflicting corrected-primary lexical identity: ' + item['sourceId'])
        connection.execute(insert(characters).values(vocabulary_id=item['sourceId'], character=item['text']).on_conflict_do_nothing())

    # These entries previously contained an empty dictionary cross-reference.
    # Keep their lexical/exposure IDs while restoring the missing English sense.
    for item in json.loads((directory / '0034_erhua_meaning_corrections.json').read_text()):
        current = connection.execute(sa.select(vocabulary).where(vocabulary.c.id == item['id'])).mappings().one()
        if current['text'] != item['text'] or current['translations'].get('en') not in {item['old'], item['new']}:
            raise RuntimeError('Pinned erhua lexical record changed: ' + item['id'])
        connection.execute(vocabulary.update().where(vocabulary.c.id == item['id']).values(
            translations={**current['translations'], 'en': item['new']}))


def downgrade():
    raise RuntimeError('Corrected teaching and learner exposure history are permanent; use a forward migration.')
