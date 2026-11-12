"""Retain the Google profile picture URL.

Revision ID: 0003_google_profile_picture
Revises: 0002_authentication
"""
import sqlalchemy as sa

from alembic import op

revision = '0003_google_profile_picture'
down_revision = '0002_authentication'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('profile_picture_url', sa.String(2048), nullable=True))


def downgrade():
    op.drop_column('users', 'profile_picture_url')
