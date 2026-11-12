"""Retain cumulative review coverage independently of compacted attempt history."""
import sqlalchemy as sa

from alembic import op

revision = '0037_assessment_coverage'
down_revision = '0036_canonical_notebook_order'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('learning_course_assessment_coverage',
        sa.Column('enrollment_id', sa.String(), sa.ForeignKey('learning_enrollments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('instance_id', sa.String(96), primary_key=True),
        sa.Column('fingerprint', sa.String(64), primary_key=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False))


def downgrade():
    raise RuntimeError('Learner evidence is retained; use a forward migration.')
