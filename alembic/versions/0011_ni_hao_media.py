"""Add on-demand stroke geometry and generated pronunciation for 你 and 好."""
import hashlib
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0011_ni_hao_media"
down_revision = "0010_wo_adult_voice"
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[1] / "data"


def tables():
    words = sa.table("learning_word_blocks", sa.column("id", sa.String),
                     sa.column("language_data", sa.JSON), sa.column("pronunciation", sa.JSON))
    strokes = sa.table("learning_character_strokes", sa.column("language_tag", sa.String),
                       sa.column("character", sa.String), sa.column("version", sa.String),
                       sa.column("data", sa.JSON))
    return words, strokes


def upgrade():
    words, strokes = tables()
    connection = op.get_bind()
    for slug in ("ni", "hao"):
        data = json.loads((DATA / f"0011_{slug}_strokes.json").read_text())
        version = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        key = dict(language_tag="cmn-Hans-CN", character=data["character"], version=version)
        exists = connection.execute(sa.select(strokes.c.version).filter_by(**key)).scalar_one_or_none()
        if exists is None:
            connection.execute(strokes.insert().values(**key, data=data))
        word_id = f"mandarin-simplified-v1-warmup-{slug}"
        row = connection.execute(sa.select(words).where(words.c.id == word_id)).mappings().one()
        metadata = dict(row["language_data"])
        metadata["strokeOrder"] = [{"format": "stroke-reference-v1", "languageTag": key["language_tag"],
                                    "character": data["character"], "version": version}]
        pronunciation = dict(row["pronunciation"])
        pronunciation["audio"] = json.loads((DATA / f"0011_{slug}_audio.json").read_text())
        connection.execute(words.update().where(words.c.id == word_id)
                           .values(language_data=metadata, pronunciation=pronunciation))


def downgrade():
    words, _ = tables()
    connection = op.get_bind()
    for slug in ("ni", "hao"):
        word_id = f"mandarin-simplified-v1-warmup-{slug}"
        row = connection.execute(sa.select(words).where(words.c.id == word_id)).mappings().one()
        metadata = dict(row["language_data"])
        metadata["strokeOrder"] = []
        pronunciation = dict(row["pronunciation"])
        pronunciation.pop("audio", None)
        connection.execute(words.update().where(words.c.id == word_id)
                           .values(language_data=metadata, pronunciation=pronunciation))
    # Keep immutable resources available to previously issued references.
