"""Expose two reviewed grammar prerequisites without changing prior evidence."""
import json
import re
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op
from app.config_loader import env

revision = '0022_notebook_grammar_vocabulary'
down_revision = '0021_notebook_grammar'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_vocabulary_characters', 'learning_word_blocks'])
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    blocks = metadata.tables['learning_word_blocks']
    rows = json.loads((Path(__file__).resolve().parents[1] / 'data/0022_notebook_grammar_vocabulary.json').read_text())
    for item in rows:
        introduction = connection.execute(sa.select(blocks).where(blocks.c.id == item['introduction_block_id'])).mappings().one()
        if introduction['sequence'] != item['introduction_sequence'] or introduction['kind'] != 'word':
            raise RuntimeError('Grammar prerequisite introduction no longer matches the notebook trail')
        connection.execute(vocabulary.insert().values(
            id=item['id'], language_tag='cmn-Hans', text=item['text'], translations=item['translations'],
            pronunciation=item['pronunciation'], level=None, rank=item['rank'],
            reading_key=re.sub(r"[\s'’]+", '', item['pronunciation']['value'].lower()),
            sense_key=item['sense_key'], language_data={
                'characters': item['characters'], 'course_word_id': item['course_word_id'],
                'introduction_block_id': item['introduction_block_id'],
                'introduction_sequence': item['introduction_sequence']}))
        connection.execute(insert(characters).values([
            {'vocabulary_id': item['id'], 'character': character} for character in item['characters']
        ]).on_conflict_do_nothing())


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Vocabulary evidence is permanent. Use a forward migration.')
    # The existing 0020 test-only teardown removes all distinct senses and their
    # evidence before restoring its earlier uniqueness constraint.
