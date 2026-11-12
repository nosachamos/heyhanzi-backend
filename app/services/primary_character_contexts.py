"""Authored word contexts for character components, independent of learner state."""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

PATH = Path(__file__).resolve().parents[1] / "data/learning/primary-character-contexts.json"


def reading(value):
    return re.sub(r"[\s'’]+", "", unicodedata.normalize("NFC", value).casefold())


@lru_cache(maxsize=1)
def contexts():
    try:
        document = json.loads(PATH.read_text())
        rows = document["items"]
        if (
            document.get("format_version") != 1
            or document.get("policy") != "bound-character-in-actually-introduced-word-v1"
            or not isinstance(rows, list)
            or not rows
        ):
            raise ValueError("Invalid current character-context policy")
        result = {}
        for row in rows:
            character = row["character"]
            word = row["context"]
            source_kind = row.get("primary_source_kind", "vocabulary")
            if (
                len(character) != 1
                or character in result
                or row["focus"] != character
                or character not in word["text"]
                or len(word["text"]) < 2
                or source_kind not in {"vocabulary", "word-block"}
                or (
                    source_kind == "word-block"
                    and row["primary_source_id"] != row["source_block_id"]
                )
                or (
                    source_kind == "vocabulary"
                    and row["primary_source_id"] == row["source_block_id"]
                )
                or not all(
                    isinstance(value, str) and value
                    for value in [
                        row["reading"],
                        row["source_block_id"],
                        row["primary_source_id"],
                        word["word_id"],
                        word["source_id"],
                        word["pinyin"],
                        word["meaning"],
                    ]
                )
            ):
                raise ValueError("Incomplete current character-context identity")
            result[character] = row
        return result
    except (OSError, KeyError, TypeError, ValueError):
        raise HTTPException(
            503, detail={"code": "learning_character_contexts_unavailable"}
        ) from None


def uses_word_context(text, pronunciation):
    row = contexts().get(text)
    return bool(
        row
        and pronunciation.get("scheme") == "pinyin"
        and reading(pronunciation.get("value", "")) == reading(row["reading"])
    )


def for_word(vocabulary_id, text, pronunciation):
    """Annotate only a visibly supplied exact word; this grants no exposure."""
    return [
        {"character": row["character"], "sourceBlockId": row["source_block_id"]}
        for row in contexts().values()
        if row["context"]["source_id"] == vocabulary_id
        and row["context"]["text"] == text
        and pronunciation.get("scheme") == "pinyin"
        and reading(row["context"]["pinyin"]) == reading(pronunciation.get("value", ""))
    ]
