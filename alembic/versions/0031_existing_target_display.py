"""Display four exact existing lexical IDs; preserve identity and exposure history."""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = '0031_existing_target_display'
down_revision = '0030_context_vocabulary'
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_word_blocks'])
    vocabulary = metadata.tables['learning_vocabulary']
    blocks = metadata.tables['learning_word_blocks']
    rows = json.loads((Path(__file__).resolve().parents[1] / 'data/0031_existing_target_display.json').read_text())
    for item in rows:
        current = connection.execute(sa.select(vocabulary).where(vocabulary.c.id == item['id'])).mappings().one()
        expected = item['expected']
        original = item['expectedDatabaseRecord']
        applied = {**original, 'language_data': {**original['language_data'], **item['display']}}
        if dict(current) not in [original, applied]:
            raise RuntimeError('Pinned original lexical record changed: ' + item['id'])
        for key in ['text', 'translations', 'pronunciation', 'rank']:
            if current[key] != expected[key]:
                raise RuntimeError('Existing lexical identity changed: ' + item['id'] + ':' + key)
        if current['sense_key'] != '' or current['language_tag'] != 'cmn-Hans':
            raise RuntimeError('Existing lexical scope changed')
        display = item['display']
        anchor = item.get('priorityAnchor') or {'block': display['introduction_block_id'], 'sequence': display['introduction_sequence']}
        block = connection.execute(sa.select(blocks).where(blocks.c.id == anchor['block'])).mappings().one()
        if block['sequence'] != anchor['sequence'] or block['kind'] != 'word' or block['text'] != item.get('anchorText', expected['text']):
            raise RuntimeError('Existing notebook anchor changed')
        data = dict(current['language_data'] or {})
        if data.get('characters') != expected['characters']:
            raise RuntimeError('Existing character scope changed')
        if any(key in data and data[key] != value for key, value in display.items()):
            raise RuntimeError('Conflicting prior display metadata')
        connection.execute(vocabulary.update().where(vocabulary.c.id == item['id']).values(language_data={**data, **display}))

def downgrade():
    raise RuntimeError('Vocabulary and learner exposure history are permanent; use a forward migration.')
