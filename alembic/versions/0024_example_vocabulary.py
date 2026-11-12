"""Add an authentic meal compound and repair two exact lexical readings."""

import json
import re
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op
from app.config_loader import env

revision = '0024_example_vocabulary'
down_revision = '0023_online_reading'
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[1] / 'data'


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_vocabulary_characters', 'learning_word_blocks'])
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    blocks = metadata.tables['learning_word_blocks']
    for item in json.loads((DATA / '0024_common_word_reading_corrections.json').read_text()):
        row = connection.execute(sa.select(vocabulary).where(vocabulary.c.id == item['id'])).mappings().one()
        if (row['text'] != item['text'] or row['translations']['en'] != item['meaning']
                or row['pronunciation']['value'] not in {item['previousPinyin'], item['pinyin']}):
            raise RuntimeError('Reading correction no longer matches its original lexical identity')
        connection.execute(vocabulary.update().where(vocabulary.c.id == item['id']).values(
            pronunciation={**row['pronunciation'], 'value': item['pinyin']},
            reading_key=re.sub(r"[\s'’]+", '', item['pinyin'].lower())))
    for item in json.loads((DATA / '0024_common_word_example_support.json').read_text()):
        introduction = connection.execute(sa.select(blocks).where(blocks.c.id == item['introduction_block_id'])).mappings().one()
        constituent_cards = connection.execute(sa.select(blocks.c.text, blocks.c.sequence).where(
            blocks.c.kind == 'word', blocks.c.text.in_(item['characters']))).all()
        if (introduction['sequence'] != item['introduction_sequence'] or introduction['kind'] != 'word'
                or {row.text for row in constituent_cards} != set(item['characters'])
                or max(row.sequence for row in constituent_cards) != item['introduction_sequence']):
            raise RuntimeError('Example support introduction must follow both constituent characters')
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
        raise RuntimeError('Vocabulary introductions and corrections are permanent. Use a forward migration.')
    connection = op.get_bind()
    vocabulary = sa.Table('learning_vocabulary', sa.MetaData(), autoload_with=connection)
    for item in json.loads((DATA / '0024_common_word_reading_corrections.json').read_text()):
        connection.execute(vocabulary.update().where(vocabulary.c.id == item['id']).values(
            pronunciation={'scheme': 'pinyin', 'value': item['previousPinyin']},
            reading_key=re.sub(r"[\s'’]+", '', item['previousPinyin'].lower())))
    # Existing 0020 TEST-only teardown removes added senses and related evidence.
