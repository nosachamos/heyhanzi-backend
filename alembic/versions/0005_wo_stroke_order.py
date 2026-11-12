"""Add sourced stroke geometry for 我 without changing enrollment or progress."""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0005_wo_stroke_order"
down_revision = "0004_learning_warmup"
branch_labels = None
depends_on = None

WORD_ID = "mandarin-simplified-v1-warmup-wo"


def update_strokes(upgrading):
    words = sa.table("learning_word_blocks", sa.column("id", sa.String),
                     sa.column("language_data", sa.JSON))
    connection = op.get_bind()
    metadata = connection.execute(sa.select(words.c.language_data).where(
        words.c.id == WORD_ID)).scalar_one()
    metadata = dict(metadata)
    strokes = [entry for entry in metadata.get("strokeOrder", [])
               if entry.get("character") != "我"]
    if upgrading:
        data = json.loads((Path(__file__).resolve().parents[1]
                           / "data/0005_wo_strokes.json").read_text())
        strokes.append(data)
    metadata["strokeOrder"] = strokes
    connection.execute(words.update().where(words.c.id == WORD_ID).values(language_data=metadata))


def upgrade():
    update_strokes(True)


def downgrade():
    update_strokes(False)
