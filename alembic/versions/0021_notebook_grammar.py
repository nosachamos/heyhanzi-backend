"""Record explicitly displayed notebook grammar explanations independently."""
import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0021_notebook_grammar'
down_revision = '0020_story_vocabulary'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('learning_grammar_exposures',
        sa.Column('enrollment_id', sa.String(36), sa.ForeignKey('learning_enrollments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('grammar_id', sa.String(96), primary_key=True),
        sa.Column('fingerprint', sa.String(64), primary_key=True),
        sa.Column('block_id', sa.String(96), sa.ForeignKey('learning_word_blocks.id'), nullable=False),
        sa.Column('displayed_at', sa.BigInteger(), nullable=False))


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Learning evidence is permanent. Use a forward migration.')
    op.drop_table('learning_grammar_exposures')
