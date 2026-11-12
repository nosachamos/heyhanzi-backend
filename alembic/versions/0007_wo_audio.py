"""Reference the pre-generated 我 pronunciation without embedding audio in paths."""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0007_wo_audio"
down_revision = "0006_character_strokes"
branch_labels = None
depends_on = None


def update_audio(upgrading):
    words = sa.table("learning_word_blocks", sa.column("id", sa.String), sa.column("pronunciation", sa.JSON))
    connection = op.get_bind()
    word_id = "mandarin-simplified-v1-warmup-wo"
    pronunciation = dict(connection.execute(sa.select(words.c.pronunciation).where(words.c.id == word_id)).scalar_one())
    if upgrading:
        pronunciation["audio"] = json.loads((Path(__file__).resolve().parents[1] / "data/0007_wo_audio.json").read_text())
    else:
        pronunciation.pop("audio", None)
    connection.execute(words.update().where(words.c.id == word_id).values(pronunciation=pronunciation))


def upgrade():
    update_audio(True)


def downgrade():
    update_audio(False)
