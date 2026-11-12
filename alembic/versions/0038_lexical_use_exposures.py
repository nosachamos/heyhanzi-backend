"""Retain exact full-scope notebook teaching displays independently of old glosses."""
import sqlalchemy as sa

from alembic import op

revision = '0038_lexical_use_exposures'
down_revision = '0037_assessment_coverage'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('learning_lexical_use_exposures',
        sa.Column('enrollment_id', sa.String(), sa.ForeignKey('learning_enrollments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vocabulary_id', sa.String(), sa.ForeignKey('learning_vocabulary.id'), primary_key=True),
        sa.Column('fingerprint', sa.String(64), primary_key=True),
        sa.Column('block_id', sa.String(), sa.ForeignKey('learning_word_blocks.id'), nullable=False),
        sa.Column('displayed_at', sa.BigInteger(), nullable=False))


def downgrade():
    raise RuntimeError('Learner display history is retained; use a forward migration.')
