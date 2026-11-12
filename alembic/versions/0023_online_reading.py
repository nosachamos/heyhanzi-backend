"""Correct 线上 pronunciation while retaining its ID, sense and learner evidence."""

import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0023_online_reading'
down_revision = '0022_notebook_grammar_vocabulary'
branch_labels = None
depends_on = None

IDENTITY = 'cmn-16b971db0c7b145ef190fa2a'
PREVIOUS = 'xiàshàng'
CORRECTED = 'xiànshàng'


def upgrade():
    connection = op.get_bind()
    vocabulary = sa.Table('learning_vocabulary', sa.MetaData(), autoload_with=connection)
    row = connection.execute(sa.select(vocabulary).where(vocabulary.c.id == IDENTITY)).mappings().one()
    if (row['text'] != '线上' or row['translations']['en'] != 'online'
            or row['pronunciation']['value'] not in {PREVIOUS, CORRECTED}):
        raise RuntimeError('线上 correction no longer matches the exact original lexical identity')
    connection.execute(vocabulary.update().where(vocabulary.c.id == IDENTITY).values(
        pronunciation={**row['pronunciation'], 'value': CORRECTED}, reading_key=CORRECTED))


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Vocabulary corrections are permanent. Use a forward migration.')
    connection = op.get_bind()
    vocabulary = sa.Table('learning_vocabulary', sa.MetaData(), autoload_with=connection)
    connection.execute(vocabulary.update().where(vocabulary.c.id == IDENTITY).values(
        pronunciation={'scheme': 'pinyin', 'value': PREVIOUS}, reading_key=PREVIOUS))
