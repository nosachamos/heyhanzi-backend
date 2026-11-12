"""Record displayed vocabulary and add missing common words used by examples."""
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op
from app.config_loader import env

revision = '0014_vocabulary_exposures'
down_revision = '0013_vocabulary_readings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('learning_vocabulary_exposures',
        sa.Column('enrollment_id', sa.String(36), sa.ForeignKey('learning_enrollments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vocabulary_id', sa.String(96), sa.ForeignKey('learning_vocabulary.id'), primary_key=True),
        sa.Column('block_id', sa.String(96), sa.ForeignKey('learning_word_blocks.id'), nullable=False),
        sa.Column('displayed_at', sa.BigInteger, nullable=False))
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_vocabulary_characters'])
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    for item in json.loads((Path(__file__).resolve().parents[1] / 'data/0014_common_vocabulary.json').read_text(encoding='utf-8')):
        reading_key = item['pronunciation']['value'].replace(' ', '')
        connection.execute(insert(vocabulary).values(
            id=item['id'], language_tag='cmn-Hans', text=item['text'], translations=item['translations'],
            pronunciation=item['pronunciation'], language_data={'characters': item['characters']},
            level=f"hsk-{item['level']}", rank=item['rank'], reading_key=reading_key).on_conflict_do_nothing())
        vocabulary_id = connection.scalar(sa.select(vocabulary.c.id).where(
            vocabulary.c.language_tag == 'cmn-Hans', vocabulary.c.text == item['text'], vocabulary.c.reading_key == reading_key))
        connection.execute(insert(characters).values(
            [{'vocabulary_id': vocabulary_id, 'character': char} for char in item['characters']]).on_conflict_do_nothing())


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Vocabulary exposure history is permanent. Use a forward migration.')
    op.drop_table('learning_vocabulary_exposures')
    # Retain the added lexical data, including during disposable schema rehearsals.
    # The TEST round trip downgrades to base before rebuilding the complete seed.
