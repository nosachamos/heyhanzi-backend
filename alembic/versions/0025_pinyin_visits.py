"""Retain the four introductory pinyin reading visits per enrollment."""
import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0025_pinyin_visits'
down_revision = '0024_example_vocabulary'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('learning_enrollments', sa.Column('pinyin_completed', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Pinyin reading history is permanent; use a forward repair.')
    op.drop_column('learning_enrollments', 'pinyin_completed')
