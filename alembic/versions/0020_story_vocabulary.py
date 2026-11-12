"""Introduce exact lexical prerequisites for the approved reading course."""
import json
import re
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op
from app.config_loader import env

revision = '0020_story_vocabulary'
down_revision = '0019_course_exercise_runs'
branch_labels = None
depends_on = None


def upgrade():
    # A familiar reading does not establish every grammatical/lexical sense.
    # Keep all existing IDs and exposures on their original default sense.
    op.add_column('learning_vocabulary', sa.Column('sense_key', sa.String(96), nullable=False, server_default=''))
    op.drop_constraint('uq_learning_vocabulary_reading', 'learning_vocabulary', type_='unique')
    op.create_unique_constraint('uq_learning_vocabulary_reading_sense', 'learning_vocabulary',
                                ['language_tag', 'text', 'reading_key', 'sense_key'])
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary', 'learning_vocabulary_characters', 'learning_word_blocks'])
    vocabulary = metadata.tables['learning_vocabulary']
    characters = metadata.tables['learning_vocabulary_characters']
    blocks = metadata.tables['learning_word_blocks']
    rows = json.loads((Path(__file__).resolve().parents[1] / 'data/0020_story_vocabulary.json').read_text())
    for item in rows:
        introduction = connection.execute(sa.select(blocks).where(blocks.c.id == item['introduction_block_id'])).mappings().one()
        if introduction['sequence'] != item['introduction_sequence'] or introduction['kind'] != 'word':
            raise RuntimeError('Story prerequisite introduction no longer matches the notebook trail')
        current = connection.execute(sa.select(vocabulary).where(vocabulary.c.id == item['id'])).mappings().first()
        if current is not None:
            if current['text'] != item['text'] or current['translations']['en'] != item['translations']['en']:
                raise RuntimeError('Story prerequisite source has changed lexical sense')
            previous = item.get('previous_pinyin', item['pronunciation']['value'])
            if current['pronunciation']['value'] not in {previous, item['pronunciation']['value']}:
                raise RuntimeError('Story prerequisite source has an unreviewed reading')
        values = dict(language_tag='cmn-Hans', text=item['text'], translations=item['translations'],
                      pronunciation=item['pronunciation'],
                      language_data={**(current['language_data'] if current else {}),
                                     'characters': item['characters'], 'course_word_id': item['course_word_id'],
                                     'introduction_block_id': item['introduction_block_id'],
                                     'introduction_sequence': item['introduction_sequence']},
                      level=current['level'] if current else None, rank=item['rank'],
                      reading_key=re.sub(r"[\s'’]+", '', item['pronunciation']['value'].lower()),
                      sense_key=item.get('sense_key', item['course_word_id']))
        # Existing source IDs retain their exposures. Citation spelling changes
        # do not replace the lexical meaning or force slow/unnatural recordings.
        if current:
            connection.execute(vocabulary.update().where(vocabulary.c.id == item['id']).values(**values))
        else:
            connection.execute(vocabulary.insert().values(id=item['id'], **values))
        connection.execute(insert(characters).values([
            {'vocabulary_id': item['id'], 'character': character} for character in item['characters']
        ]).on_conflict_do_nothing())


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Vocabulary introductions are permanent. Use a forward migration.')
    # TEST schema disposal proceeds to base. Remove the new sense constraint
    # and column so the earlier reading migration can drop its own constraint.
    # New lexical senses are never deleted from production through downgrade.
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM learning_vocabulary_exposures WHERE vocabulary_id IN "
                               "(SELECT id FROM learning_vocabulary WHERE sense_key <> '')"))
    connection.execute(sa.text("DELETE FROM learning_vocabulary_characters WHERE vocabulary_id IN "
                               "(SELECT id FROM learning_vocabulary WHERE sense_key <> '')"))
    connection.execute(sa.text("DELETE FROM learning_vocabulary WHERE sense_key <> ''"))
    op.drop_constraint('uq_learning_vocabulary_reading_sense', 'learning_vocabulary', type_='unique')
    op.drop_column('learning_vocabulary', 'sense_key')
    op.create_unique_constraint('uq_learning_vocabulary_reading', 'learning_vocabulary',
                                ['language_tag', 'text', 'reading_key'])
