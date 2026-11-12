"""Expand dictionary shorthand before presenting English definitions to learners."""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_MANDARIN_MEANINGS = {
    (entry["text"], entry["pinyin"]): entry["meaning"]
    for entry in json.loads(
        (Path(__file__).resolve().parents[1] / "data/learning/mandarin-meanings.json").read_text(
            encoding="utf-8"
        )
    )
}

_EXPANSIONS = {
    "sb": "somebody",
    "sth": "something",
    "abbr.": "abbreviation",
    "coll.": "informal",
    "fig.": "figuratively",
    "lit.": "literally",
    "usu.": "usually",
    "esp.": "especially",
    "e.g.": "for example",
    "i.e.": "that is",
    "pr.": "pronunciation",
    "CL:": "classifier:",
}
_SHORTHAND = re.compile(
    r"(?<!\w)(?:CL:|(?:"
    + "|".join(re.escape(key) for key in _EXPANSIONS if key != "CL:")
    + r")(?!\w))"
)


def plain_english(definition):
    # Whole tokens preserve words such as "substantial", acronyms and possessives.
    return _SHORTHAND.sub(lambda match: _EXPANSIONS[match[0]], definition)


def learner_translations(translations, *, text=None, pronunciation=None):
    """Return display copy without mutating stored/source data or other locales."""
    if not isinstance(translations.get("en"), str):
        return translations
    meaning = None
    if pronunciation and pronunciation.get("scheme") == "pinyin":
        meaning = _MANDARIN_MEANINGS.get((text, pronunciation.get("value", "").strip()))
    return {**translations, "en": meaning or plain_english(translations["en"])}


@lru_cache(maxsize=1)
def word_meaning_corrections():
    """Reviewed ID-scoped display corrections; historical seed data stays intact."""
    corrections = {}
    directory = Path(__file__).resolve().parents[1] / "data/learning"
    for reviewer in ("a", "b", "c", "course"):
        path = directory / f"common-word-meaning-corrections-review-{reviewer}.json"
        if not path.exists():
            continue
        for entry in json.loads(path.read_text(encoding="utf-8")):
            if any(
                not isinstance(entry.get(key), str) or not entry[key].strip()
                for key in ("id", "old", "new", "reason")
            ):
                raise ValueError(f"Invalid common-word meaning correction in {path.name}")
            previous = corrections.get(entry["id"])
            if previous and (previous["old"], previous["new"]) != (entry["old"], entry["new"]):
                raise ValueError(f"Conflicting common-word meaning corrections: {entry['id']}")
            corrections[entry["id"]] = entry
    return corrections


def word_translations(translations, *, vocabulary_id, text=None, pronunciation=None, sense_key=""):
    # A separately introduced sense must never inherit a surface-form override.
    if sense_key:
        return translations
    displayed = learner_translations(translations, text=text, pronunciation=pronunciation)
    correction = word_meaning_corrections().get(vocabulary_id)
    exact_reading = (
        not correction
        or not correction.get("reading")
        or (
            pronunciation
            and pronunciation.get("scheme") == "pinyin"
            and (not correction.get("text") or text == correction["text"])
            and _reading_key(pronunciation.get("value", "")) == _reading_key(correction["reading"])
        )
    )
    if correction and exact_reading and displayed.get("en") == correction["old"]:
        return {**displayed, "en": correction["new"]}
    return displayed


def _reading_key(value):
    return re.sub(r"[\s'’\-]+", "", unicodedata.normalize("NFC", value).lower())


@lru_cache(maxsize=1)
def primary_character_corrections():
    """Pinned current character-card readings, never general dictionary overrides."""
    path = Path(__file__).resolve().parents[1] / "data/learning/primary-character-corrections.json"
    return {
        entry["text"]: entry
        for entry in json.loads(path.read_text(encoding="utf-8"))["corrections"]
    }


def primary_pronunciation(text, original):
    """Select the intended first teaching reading without retaining stale audio."""
    correction = primary_character_corrections().get(text)
    if (
        not correction
        or original.get("scheme") != "pinyin"
        or _reading_key(original.get("value", ""))
        not in {
            _reading_key(correction["previous"]["pronunciation"]["value"]),
            _reading_key(correction["pronunciation"]["value"]),
        }
    ):
        return original
    if original.get("value") == correction["pronunciation"]["value"]:
        return original
    return {
        key: value
        for key, value in {**original, **correction["pronunciation"]}.items()
        if key != "audio"
    }


def primary_character_display(row):
    """Project immutable seed characters into current, explicitly identified teaching."""
    correction = primary_character_corrections().get(row["text"])
    if not correction:
        return {
            **row,
            "translations": primary_translations(
                row["translations"], text=row["text"], pronunciation=row["pronunciation"]
            ),
        }
    previous = correction["previous"]["translations"]
    expanded_previous = {**previous, "en": plain_english(previous["en"])}
    if row.get("translations") not in [
        previous,
        expanded_previous,
        correction["translations"],
    ] or row.get("pronunciation", {}).get("value") not in {
        correction["previous"]["pronunciation"]["value"],
        correction["pronunciation"]["value"],
    }:
        raise ValueError("Primary correction no longer matches its pinned source: " + row["text"])
    return {
        **row,
        "translations": primary_translations(
            correction["translations"], text=row["text"], pronunciation=correction["pronunciation"]
        ),
        "pronunciation": correction["pronunciation"],
        "primary_lexical_display": {
            "source_id": correction["sourceId"],
            "course_word_id": correction["courseWordId"],
            "introduction_block_id": correction["blockId"],
            "introduction_block_sequence": correction["sequence"],
        },
    }


@lru_cache(maxsize=1)
def primary_meaning_corrections():
    """Compiler-pinned bridge from reviewed lexical IDs to exact primary readings."""
    path = (
        Path(__file__).resolve().parents[1]
        / "data/learning/common-word-primary-meaning-corrections.json"
    )
    if not path.exists():
        return {}
    reviewed = word_meaning_corrections()
    result = {}
    for entry in json.loads(path.read_text(encoding="utf-8")):
        correction = reviewed.get(entry["vocabularyId"])
        # A stale derived map cannot override a newer reviewed meaning.
        if not correction or (entry["old"], entry["new"]) != (correction["old"], correction["new"]):
            continue
        key = (entry["text"], _reading_key(entry["reading_key"]), entry["old"])
        if key in result and result[key] != entry["new"]:
            raise ValueError("Conflicting primary-reading meaning corrections")
        result[key] = entry["new"]
    return result


def primary_translations(translations, *, text=None, pronunciation=None):
    correction = primary_character_corrections().get(text)
    if (
        correction
        and pronunciation
        and pronunciation.get("scheme") == "pinyin"
        and _reading_key(pronunciation.get("value", ""))
        in {
            _reading_key(correction["previous"]["pronunciation"]["value"]),
            _reading_key(correction["pronunciation"]["value"]),
        }
    ):
        return readable_primary_cue(
            {**translations, "en": correction["translations"]["en"]},
            text,
            correction["pronunciation"],
        )
    displayed = learner_translations(translations, text=text, pronunciation=pronunciation)
    if not pronunciation or pronunciation.get("scheme") != "pinyin":
        return displayed
    key = (text, _reading_key(pronunciation.get("value", "")), displayed.get("en"))
    corrected = primary_meaning_corrections().get(key)
    return readable_primary_cue(
        {**displayed, "en": corrected} if corrected else displayed, text, pronunciation
    )


@lru_cache(maxsize=1)
def primary_cue_projections():
    """Copy-only clarification; lexical readings and historical identity stay exact."""
    path = Path(__file__).resolve().parents[1] / "data/learning/primary-character-cues.json"
    if not path.exists():
        return {}
    return {
        (row["text"], _reading_key(row["pinyin"]), row["old"]): row["new"]
        for row in json.loads(path.read_text(encoding="utf-8"))["items"]
    }


def readable_primary_cue(translations, text, pronunciation):
    if not pronunciation or pronunciation.get("scheme") != "pinyin":
        return translations
    key = (text, _reading_key(pronunciation.get("value", "")), translations.get("en"))
    replacement = primary_cue_projections().get(key)
    return {**translations, "en": replacement} if replacement else translations
