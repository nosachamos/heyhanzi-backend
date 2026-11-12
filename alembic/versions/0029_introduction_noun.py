"""Introduce noun 介绍 separately from the existing verbal lexical use."""
import json
import re
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op

revision = '0029_introduction_noun'
down_revision = '0028_notice_noun_vocabulary'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_vocabulary_characters', 'learning_word_blocks'])
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    blocks = metadata.tables['learning_word_blocks']
    rows = json.loads((Path(__file__).resolve().parents[1] / 'data/0029_introduction_noun.json').read_text())
    for item in rows:
        introduction = connection.execute(sa.select(blocks).where(blocks.c.id == item['introduction_block_id'])).mappings().one()
        if introduction['sequence'] != item['introduction_sequence'] or introduction['kind'] != 'word':
            raise RuntimeError('Introduction-noun vocabulary introduction no longer matches the notebook trail')
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
    raise RuntimeError('Vocabulary and learner exposure history are permanent; use a forward migration.')
