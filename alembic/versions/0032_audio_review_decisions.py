"""Keep native-speaker listening approvals outside deployment artifacts."""
import sqlalchemy as sa

from alembic import op

revision = '0032_audio_review_decisions'
down_revision = '0031_existing_target_display'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('audio_review_decisions',
        sa.Column('review_key', sa.String(64), primary_key=True),
        sa.Column('approved', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('reviewed_at', sa.BigInteger(), nullable=False))
    op.create_table('audio_review_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('review_key', sa.String(64), sa.ForeignKey('audio_review_decisions.review_key'), nullable=False),
        sa.Column('reviewer_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('approved', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('reviewed_at', sa.BigInteger(), nullable=False),
        sa.Column('recording', sa.JSON(), nullable=False))
    op.create_index('ix_audio_review_events_review_key', 'audio_review_events', ['review_key'])


def downgrade():
    raise RuntimeError('Listening decisions and audit history are permanent; use a forward migration.')
