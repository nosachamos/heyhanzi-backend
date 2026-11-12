"""Expand practical Mandarin vocabulary and sentence prerequisites across HSK 1–6."""
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op
from app.config_loader import env

revision = '0016_practical_vocabulary'
down_revision = '0015_expanded_vocabulary'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_vocabulary_characters'])
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    for item in json.loads((Path(__file__).resolve().parents[1] / 'data/0016_practical_vocabulary.json').read_text(encoding='utf-8')):
        reading_key = item['pronunciation']['value'].replace(' ', '')
        connection.execute(insert(vocabulary).values(
            id=item['id'], language_tag='cmn-Hans', text=item['text'], translations=item['translations'],
            pronunciation=item['pronunciation'], language_data={'characters': item['characters']},
            level=None, rank=item['rank'], reading_key=reading_key).on_conflict_do_nothing())
        vocabulary_id = connection.scalar(sa.select(vocabulary.c.id).where(
            vocabulary.c.language_tag == 'cmn-Hans', vocabulary.c.text == item['text'], vocabulary.c.reading_key == reading_key))
        connection.execute(insert(characters).values([
            {'vocabulary_id': vocabulary_id, 'character': char} for char in item['characters']]).on_conflict_do_nothing())


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Vocabulary additions are permanent. Use a forward migration.')
    # TEST rehearsals retain lexical rows until the schema is dropped at base.
    # Reapplying this revision is idempotent and preserves exposure references.
