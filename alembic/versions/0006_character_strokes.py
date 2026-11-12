"""Extract character geometry into immutable, language-specific resources."""
import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "0006_character_strokes"
down_revision = "0005_wo_stroke_order"
branch_labels = None
depends_on = None


def tables():
    words = sa.table("learning_word_blocks", sa.column("id", sa.String),
                     sa.column("language_data", sa.JSON))
    strokes = sa.table("learning_character_strokes", sa.column("language_tag", sa.String),
                       sa.column("character", sa.String), sa.column("version", sa.String),
                       sa.column("data", sa.JSON))
    return words, strokes


def upgrade():
    op.create_table("learning_character_strokes",
        sa.Column("language_tag", sa.String(32), primary_key=True),
        sa.Column("character", sa.String(8), primary_key=True),
        sa.Column("version", sa.String(64), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False))
    words, strokes = tables()
    connection = op.get_bind()
    inserted = set()
    for row in connection.execute(sa.select(words)).mappings().all():
        metadata = dict(row["language_data"])
        if metadata.get("kind") != "mandarin-simplified" or not metadata.get("strokeOrder"):
            continue
        references = []
        for data in metadata["strokeOrder"]:
            language = "cmn-Hans-CN"
            version = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
            key = (language, data["character"], version)
            if key not in inserted:
                connection.execute(strokes.insert().values(language_tag=language,
                    character=data["character"], version=version, data=data))
                inserted.add(key)
            references.append({"format": "stroke-reference-v1", "languageTag": language,
                               "character": data["character"], "version": version})
        metadata["strokeOrder"] = references
        connection.execute(words.update().where(words.c.id == row["id"])
                           .values(language_data=metadata))


def downgrade():
    words, strokes = tables()
    connection = op.get_bind()
    for row in connection.execute(sa.select(words)).mappings().all():
        metadata = dict(row["language_data"])
        references = metadata.get("strokeOrder", [])
        if not references or metadata.get("kind") != "mandarin-simplified":
            continue
        metadata["strokeOrder"] = [connection.execute(sa.select(strokes.c.data).where(
            strokes.c.language_tag == ref["languageTag"], strokes.c.character == ref["character"],
            strokes.c.version == ref["version"])).scalar_one() for ref in references]
        connection.execute(words.update().where(words.c.id == row["id"])
                           .values(language_data=metadata))
    op.drop_table("learning_character_strokes")
