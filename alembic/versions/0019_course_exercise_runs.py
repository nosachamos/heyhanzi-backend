"""Add course exercise attempts without changing existing learning history."""
import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = "0019_course_exercise_runs"
down_revision = "0018_all_character_strokes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("learning_course_exercise_runs",
        sa.Column("enrollment_id", sa.String(36), sa.ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("instance_id", sa.String(96), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("prepared", sa.Boolean, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("draft", sa.JSON, nullable=False),
        sa.Column("supports", sa.JSON, nullable=False),
        sa.Column("attempts", sa.JSON, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False))
    op.create_index("ix_learning_course_exercise_runs_instance_id", "learning_course_exercise_runs", ["instance_id"])


def downgrade():
    if env.config["GENERAL"]["Environment"] != "TEST":
        raise RuntimeError("Exercise attempts are permanent. Use a forward migration.")
    op.drop_table("learning_course_exercise_runs")
