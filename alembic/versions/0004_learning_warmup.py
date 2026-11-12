"""Add language tracks, warm-up vocabulary and persistent progress.

Revision ID: 0004_learning_warmup
Revises: 0003_google_profile_picture
"""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0004_learning_warmup"
down_revision = "0003_google_profile_picture"
branch_labels = None
depends_on = None


def upgrade():
    tracks = op.create_table("learning_tracks",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("language_tag", sa.String(32), nullable=False),
        sa.Column("names", sa.JSON(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("framework", sa.String(32)))
    stages = op.create_table("learning_stages",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("track_id", sa.String(64), sa.ForeignKey("learning_tracks.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("names", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("level", sa.String(32)),
        sa.UniqueConstraint("track_id", "position"))
    op.create_index("ix_learning_stages_track_id", "learning_stages", ["track_id"])
    words = op.create_table("learning_word_blocks",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("stage_id", sa.String(80), sa.ForeignKey("learning_stages.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("text", sa.String(128), nullable=False),
        sa.Column("translations", sa.JSON(), nullable=False), sa.Column("pronunciation", sa.JSON(), nullable=False),
        sa.Column("language_data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("stage_id", "position"), sa.CheckConstraint("position > 0", name="positive_position"))
    op.create_index("ix_learning_word_blocks_stage_id", "learning_word_blocks", ["stage_id"])
    op.create_table("learning_enrollments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track_id", sa.String(64), sa.ForeignKey("learning_tracks.id"), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False), sa.UniqueConstraint("user_id", "track_id"))
    op.create_index("ix_learning_enrollments_user_id", "learning_enrollments", ["user_id"])
    op.create_table("learning_word_completions",
        sa.Column("enrollment_id", sa.String(36), sa.ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("block_id", sa.String(96), sa.ForeignKey("learning_word_blocks.id"), primary_key=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=False))
    # Frozen seed travels with the migration; future curriculum edits use a new revision.
    data = json.loads((Path(__file__).resolve().parents[1] / "data/0004_warmup.json").read_text())
    op.bulk_insert(tracks, [data["track"]])
    op.bulk_insert(stages, [data["stage"]])
    op.bulk_insert(words, data["words"])


def downgrade():
    for table in ["learning_word_completions", "learning_enrollments", "learning_word_blocks", "learning_stages", "learning_tracks"]:
        op.drop_table(table)
