"""Reconcile actual character/word order while preserving IDs and all histories."""

import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0036_canonical_notebook_order"
down_revision = "0035_core_notebook_vocabulary"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(
        connection,
        only=["learning_tracks", "learning_stages", "learning_word_blocks", "learning_vocabulary"],
    )
    tracks = metadata.tables["learning_tracks"]
    stages = metadata.tables["learning_stages"]
    blocks = metadata.tables["learning_word_blocks"]
    vocabulary = metadata.tables["learning_vocabulary"]
    directory = Path(__file__).resolve().parents[1] / "data"
    order = json.loads((directory / "0036_notebook_order.json").read_text())
    lexical = json.loads((directory / "0036_current_vocabulary_introductions.json").read_text())
    by_id = {row["blockId"]: row for row in order["cards"]}
    content_directory = directory.parents[1] / "app/data/learning"
    catalog = json.loads((content_directory / "course-index.json").read_text())
    occurrences = {item["id"]: item for item in catalog["exercises"]}
    placements = catalog.get("learner_policy", {}).get("trail_placements", {})
    if set(placements) != set(occurrences):
        raise RuntimeError(
            "Export the complete current course catalog before migrating notebook order"
        )
    for instance_id, placement in placements.items():
        if (
            occurrences[instance_id].get("introductoryAnchor") == "INTRO-PINYIN-SOUNDS"
            and placement.get("afterBlockId") == "INTRO-PINYIN-SOUNDS"
            and placement.get("afterSequence") == 0
        ):
            continue
        card = by_id.get(placement.get("afterBlockId"))
        if (
            card is None
            or card["sequence"] != placement.get("afterSequence")
            or card["levelId"] != placement.get("levelId")
        ):
            raise RuntimeError(
                "Export current notebook placements before migrating: " + instance_id
            )
    grammar = json.loads((content_directory / "notebook-grammar.json").read_text())
    for note in grammar["items"]:
        card = by_id.get(note["block_id"])
        if card is None or card["sequence"] != note["block_sequence"]:
            raise RuntimeError("Export current notebook grammar before migrating: " + note["id"])
    track_id = order["trackId"]
    track = connection.execute(sa.select(tracks).where(tracks.c.id == track_id)).mappings().one()
    old_stages = (
        connection.execute(
            sa.select(stages).where(stages.c.track_id == track_id).order_by(stages.c.position)
        )
        .mappings()
        .all()
    )
    old_blocks = (
        connection.execute(
            sa.select(blocks).where(blocks.c.stage_id.in_([row["id"] for row in old_stages]))
        )
        .mappings()
        .all()
    )
    old_by_id = {row["id"]: row for row in old_blocks}
    if {row["blockId"] for row in order["cards"]} != {
        row["id"] for row in old_blocks if row["kind"] == "word"
    }:
        raise RuntimeError(
            "Canonical order must retain every existing character block exactly once"
        )
    # Free both uniqueness domains before moving stable rows. No learner-owned
    # completion, lexical/grammar exposure or exercise-result table is changed.
    connection.execute(
        stages.update()
        .where(stages.c.track_id == track_id)
        .values(position=stages.c.position + 1000000)
    )
    connection.execute(
        blocks.update()
        .where(blocks.c.id.in_(old_by_id))
        .values(position=blocks.c.position + 1000000)
    )
    active_ids = set()
    for row in order["stages"]:
        active_ids.add(row["id"])
        values = {**row, "track_id": track_id}
        if any(stage["id"] == row["id"] for stage in old_stages):
            connection.execute(stages.update().where(stages.c.id == row["id"]).values(**values))
        else:
            connection.execute(stages.insert().values(**values))
    retired_position = len(order["stages"])
    for row in old_stages:
        if row["id"] in active_ids:
            continue
        retired_position += 1
        connection.execute(
            stages.update().where(stages.c.id == row["id"]).values(position=retired_position)
        )
    for row in order["cards"]:
        old = old_by_id[row["blockId"]]
        if old["sequence"] != row["legacy"]["sequence"] or old["kind"] != "word":
            raise RuntimeError("Pinned historical notebook sequence changed: " + row["blockId"])
        data = dict(old["language_data"])
        data.update(
            previous_notebook_order={
                "stage_id": old["stage_id"],
                "position": old["position"],
                "sequence": old["sequence"],
            },
            notebook_order_revision=order["revision"],
            canonical_unit_id=row["unitId"],
        )
        connection.execute(
            blocks.update()
            .where(blocks.c.id == row["blockId"])
            .values(
                stage_id=row["stageId"],
                position=row["position"],
                sequence=row["sequence"],
                language_data=data,
            )
        )
    # Retired Read rows keep IDs, stages, text and all stored completions. They
    # remain outside the current authoring card inventory and never grant progress.
    for index, old in enumerate(row for row in old_blocks if row["kind"] != "word"):
        connection.execute(
            blocks.update()
            .where(blocks.c.id == old["id"])
            .values(
                position=1000000 + old["position"],
                sequence=len(order["cards"]) + index + 1,
                language_data={
                    **old["language_data"],
                    "retired": True,
                    "previous_notebook_order": {
                        "stage_id": old["stage_id"],
                        "position": old["position"],
                        "sequence": old["sequence"],
                    },
                },
            )
        )
    # Translate all explicit existing teaching boundaries by stable block ID.
    for current in connection.execute(sa.select(vocabulary)).mappings():
        data = dict(current["language_data"] or {})
        if data.get("introduction_block_id") in by_id:
            data.setdefault(
                "previous_display_metadata",
                {
                    key: value
                    for key, value in data.items()
                    if key in {"introduction_block_id", "introduction_sequence", "course_word_id"}
                },
            )
            data["introduction_sequence"] = by_id[data["introduction_block_id"]]["sequence"]
            connection.execute(
                vocabulary.update()
                .where(vocabulary.c.id == current["id"])
                .values(language_data=data)
            )
    for item in lexical["items"]:
        current = (
            connection.execute(sa.select(vocabulary).where(vocabulary.c.id == item["source_id"]))
            .mappings()
            .one()
        )
        if (
            current["text"] != item["text"]
            or current["pronunciation"]["value"] != item["source_pinyin"]
            or current["translations"]["en"]
            not in {item["source_meaning"], item.get("current_meaning", item["source_meaning"])}
        ):
            raise RuntimeError("Pinned current lexical reading/sense changed: " + item["source_id"])
        data = dict(current["language_data"] or {})
        data.setdefault(
            "previous_display_metadata",
            {
                key: value
                for key, value in data.items()
                if key in {"introduction_block_id", "introduction_sequence", "course_word_id"}
            },
        )
        data.update(
            introduction_block_id=item["block"],
            introduction_sequence=item["sequence"],
            course_word_id=data.get("course_word_id") or item.get("word_id") or item["source_id"],
            current_canonical_word_ids=item.get("word_ids", []),
            notebook_order_revision=order["revision"],
        )
        connection.execute(
            vocabulary.update()
            .where(vocabulary.c.id == item["source_id"])
            .values(language_data=data)
        )
    curriculum = dict(track["curriculum"] or {})
    curriculum.update(
        previous_ordering={"version": track["version"], "curriculum": track["curriculum"]},
        orderManifest=order["source"],
        orderRevision=order["revision"],
        canonicalUnits=len(order["units"]),
        unitSize=None,
        storyEvery=None,
        historyCompatibility=order["compatibility"],
    )
    connection.execute(
        tracks.update()
        .where(tracks.c.id == track_id)
        .values(version=track["version"] + 1, curriculum=curriculum)
    )


def downgrade():
    raise RuntimeError(
        "Canonical ordering and learner history are permanent; use a forward migration."
    )
