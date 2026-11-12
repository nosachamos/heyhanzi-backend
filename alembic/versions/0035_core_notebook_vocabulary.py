"""Install selected lexical teaching on existing cards; never grant exposure."""

import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op

revision = "0035_core_notebook_vocabulary"
down_revision = "0034_primary_character_corrections"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(
        connection,
        only=["learning_vocabulary", "learning_vocabulary_characters", "learning_word_blocks"],
    )
    vocabulary = metadata.tables["learning_vocabulary"]
    characters = metadata.tables["learning_vocabulary_characters"]
    blocks = metadata.tables["learning_word_blocks"]
    document = json.loads(
        (
            Path(__file__).resolve().parents[1] / "data/0035_core_notebook_vocabulary.json"
        ).read_text()
    )
    for record in document["items"]:
        data = record["language_data"]
        block = (
            connection.execute(
                sa.select(blocks).where(blocks.c.id == data["introduction_block_id"])
            )
            .mappings()
            .one()
        )
        if block["kind"] != "word" or block["sequence"] != data["introduction_sequence"]:
            raise RuntimeError("Pinned pre-reorder notebook anchor changed: " + record["id"])
        current = (
            connection.execute(sa.select(vocabulary).where(vocabulary.c.id == record["id"]))
            .mappings()
            .one_or_none()
        )
        if current is None:
            connection.execute(vocabulary.insert().values(**record))
        elif any(current[key] != value for key, value in record.items()):
            raise RuntimeError("Conflicting selected lexical teaching identity: " + record["id"])
        connection.execute(
            insert(characters)
            .values(
                [{"vocabulary_id": record["id"], "character": char} for char in data["characters"]]
            )
            .on_conflict_do_nothing()
        )


def downgrade():
    raise RuntimeError(
        "Lexical teaching and exposure history are permanent; use a forward migration."
    )
