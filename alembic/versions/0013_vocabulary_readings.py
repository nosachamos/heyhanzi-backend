"""Normalize lexical readings and remove duplicate display entries without touching progress."""
import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0013_vocabulary_readings'
down_revision = '0012_learning_curriculum'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('learning_vocabulary', sa.Column('reading_key', sa.String(128)))
    connection = op.get_bind()
    # 老婆 normally uses a neutral second syllable in standard Mandarin. Its
    # character 婆 still has the standalone pó reading in the character lesson.
    connection.execute(sa.text("UPDATE learning_vocabulary SET pronunciation = json_build_object('scheme', 'pinyin', 'value', 'lǎo po') WHERE id = 'cmn-6fcef30bb02f8043e1a7d56b'"))
    connection.execute(sa.text("""UPDATE learning_vocabulary SET reading_key = regexp_replace(lower(pronunciation->>'value'), '[[:space:]''’]+', '', 'g')"""))
    duplicate_ids = list(connection.scalars(sa.text('''
        SELECT id FROM (
            SELECT id, row_number() OVER (
                PARTITION BY language_tag, text, reading_key ORDER BY rank DESC, id
            ) AS ordinal FROM learning_vocabulary
        ) ranked WHERE ordinal > 1
    ''')))
    if duplicate_ids:
        statement = sa.text('DELETE FROM learning_vocabulary_characters WHERE vocabulary_id IN :ids').bindparams(sa.bindparam('ids', expanding=True))
        connection.execute(statement, {'ids': duplicate_ids})
        statement = sa.text('DELETE FROM learning_vocabulary WHERE id IN :ids').bindparams(sa.bindparam('ids', expanding=True))
        connection.execute(statement, {'ids': duplicate_ids})
    op.alter_column('learning_vocabulary', 'reading_key', nullable=False)
    op.create_unique_constraint('uq_learning_vocabulary_reading', 'learning_vocabulary', ['language_tag', 'text', 'reading_key'])


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Vocabulary corrections are permanent. Use a forward migration.')
    op.drop_constraint('uq_learning_vocabulary_reading', 'learning_vocabulary', type_='unique')
    op.drop_column('learning_vocabulary', 'reading_key')
