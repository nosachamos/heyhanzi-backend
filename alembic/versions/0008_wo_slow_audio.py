"""Use the slower female 我 pronunciation; retain the previous immutable asset."""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0008_wo_slow_audio"
down_revision = "0007_wo_audio"
branch_labels = None
depends_on = None


def set_audio(filename):
    words = sa.table("learning_word_blocks", sa.column("id", sa.String), sa.column("pronunciation", sa.JSON))
    connection = op.get_bind()
    word_id = "mandarin-simplified-v1-warmup-wo"
    pronunciation = dict(connection.execute(sa.select(words.c.pronunciation).where(words.c.id == word_id)).scalar_one())
    pronunciation["audio"] = json.loads((Path(__file__).resolve().parents[1] / "data" / filename).read_text())
    connection.execute(words.update().where(words.c.id == word_id).values(pronunciation=pronunciation))


def upgrade():
    set_audio("0008_wo_slow_audio.json")


def downgrade():
    set_audio("0007_wo_audio.json")
