"""Reviewed-shape Mandarin sentence metadata, loaded per card rather than in bulk.

Static language data may be cached. Learner selection/exposure always remains
request scoped. The bounded LRU avoids importing the whole corpus into workers.
"""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from app.services.learning_glosses import primary_translations, word_translations
from app.services.sentence_audio import current_reference
from app.services.sentence_audio import reference as audio_reference

_DIRECTORY = Path(__file__).resolve().parents[1] / "data/learning/examples"
_COMMON_DIRECTORY = _DIRECTORY.parent / "common-word-examples"
_INDEX = json.loads((_DIRECTORY / "index.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=128)
def _file(name):
    # Names come only from our checked-in index, never from a URL or user input.
    return json.loads((_DIRECTORY / name).read_text(encoding="utf-8"))


def reading_key(value):
    return re.sub(r"[\s'’\-]+", "", unicodedata.normalize("NFC", value).lower())


@lru_cache(maxsize=1)
def _common_index():
    return json.loads((_COMMON_DIRECTORY / "index.json").read_text(encoding="utf-8"))["words"]


@lru_cache(maxsize=32)
def _common_file(name):
    return json.loads((_COMMON_DIRECTORY / name).read_text(encoding="utf-8"))


def vocabulary_identity(item):
    return {"id": item.id, "reading_key": item.reading_key, "sense_key": item.sense_key}


def common_entries(text, pronunciation, vocabulary):
    """A written form alone cannot select another reading's or sense's example."""
    record = _common_index().get(vocabulary["id"])
    if not record or record["text"] != text or record["sense_key"] != vocabulary["sense_key"]:
        return []
    expected = reading_key(record["reading_key"])
    allowed_readings = set(expected.split("/"))
    supplied_readings = set(reading_key(pronunciation.get("value", "")).split("/"))
    if expected != reading_key(vocabulary["reading_key"]) or not supplied_readings.issubset(allowed_readings):
        return []
    return [entry for entry in _common_file(record["file"]).get(vocabulary["id"], [])
            if valid_common_target(entry, text, expected, vocabulary["id"])]


def valid_common_target(entry, text, expected_reading, vocabulary_id):
    if entry.get("targetVocabularyId") != vocabulary_id:
        return False
    span = entry.get("targetSpan")
    if span is None:
        return any(token["text"] == text and token.get("vocabularyId") == vocabulary_id
                   for token in entry["tokens"])
    index, start, end = span.get("tokenIndex"), span.get("start"), span.get("end")
    if not all(isinstance(value, int) for value in (index, start, end)):
        return False
    if not 0 <= index < len(entry["tokens"]):
        return False
    token = entry["tokens"][index]
    return (0 <= start < end <= len(token["text"]) and token["text"][start:end] == text and
            span.get("text") == text and span.get("vocabularyId") == vocabulary_id and
            span_reading_matches(text, span.get("pinyin", ""), expected_reading))


def span_reading_matches(text, spoken_reading, expected_reading):
    allowed = {reading_key(value) for value in expected_reading.split("/")}
    # These documented surface realizations preserve the same lexical identity;
    # tones on arbitrary homographs remain distinct.
    surface_forms = {"一": {"yī", "yí", "yì"}, "不": {"bù", "bú"}, "个": {"gè", "ge"}}
    if text in surface_forms and allowed & surface_forms[text]:
        allowed |= surface_forms[text]
    return reading_key(spoken_reading) in allowed


def entries(text, pronunciation, vocabulary=None):
    if pronunciation.get("scheme") != "pinyin":
        return []
    if vocabulary is not None:
        return common_entries(text, pronunciation, vocabulary)
    if text in _INDEX["characters"]:
        return _file(_INDEX["characters"][text])
    result = []
    seen = set()
    for name in _INDEX["words"].get(text, []):
        for entry in _file(name):
            sentence = "".join(token["text"] for token in entry["tokens"])
            if sentence not in seen and any(token["text"] == text for token in entry["tokens"]):
                result.append(entry)
                seen.add(sentence)
            if len(result) == 3:
                return result
    return result


def candidates(text, pronunciation, vocabulary=None):
    return [[token["text"] for token in entry["tokens"]] for entry in entries(text, pronunciation, vocabulary)]


def is_word(token):
    return any("\u3400" <= character <= "\u9fff" for character in token)


def example_targets():
    return _INDEX["characters"].keys() | _INDEX["words"].keys()


def required_words(subjects, *, known_grammar=None):
    # A bound target stays a separately introduced identity even when its only
    # visible occurrence is a span inside a whole compound token.
    subjects = tuple(subjects)
    return {subject[0] for subject in subjects} | {
        token["text"] for subject in subjects for entry in entries(*subject)
        if entry.get('knowledgeStatus') != 'unresolved' and (known_grammar is None
            or set(entry.get('requiresGrammar', [])) <= known_grammar)
        for token in entry["tokens"] if is_word(token["text"])
    }


def required_vocabulary_ids(subjects, *, known_grammar=None):
    """Explicit whole-base proofs can be prerequisites without visible tokens."""
    return {source_id for subject in subjects for entry in entries(*subject)
        if entry.get('knowledgeStatus') != 'unresolved' and (known_grammar is None
            or set(entry.get('requiresGrammar', [])) <= known_grammar)
        for source_id in entry.get('requiresVocabularyIds', [])}


def entry_available(entry, known_words, known_vocabulary_ids, known_grammar=frozenset()):
    if (entry.get('knowledgeStatus') == 'unresolved'
            or not set(entry.get('requiresGrammar', [])) <= known_grammar
            or not set(entry.get('requiresVocabularyIds', [])) <= known_vocabulary_ids):
        return False
    if entry.get('mediaRequirement') == 'current-paired-word-timed-voices-v1':
        # Revised speech never inherits an old sentence recording or a one-voice
        # fallback. Ordinary inventory still sees this authored entry and can
        # produce its missing media before it becomes a playable example.
        audio = current_reference(''.join(token['text'] for token in entry['tokens']), entry['pinyin'],
            word_texts=[token['text'] for token in entry['tokens'] if token.get('pinyin')])
        if not audio or any(not audio.get('voices', {}).get('mandarin-' + voice, {}).get('readAlong')
                            for voice in ['female', 'male']):
            return False
    span = entry.get("targetSpan")
    if span and span["vocabularyId"] not in known_vocabulary_ids:
        return False
    return all(
        not is_word(token["text"]) or (
            token["text"] in known_words and
            (not token.get("vocabularyId") or token["vocabularyId"] in known_vocabulary_ids)
        ) for token in entry["tokens"]
    )


def preferred_entries(text, pronunciation, available_words, limit=3, *, vocabulary=None,
                      available_vocabulary_ids=frozenset(), known_grammar=frozenset()):
    return [entry for entry in entries(text, pronunciation, vocabulary)
            if entry_available(entry, available_words, available_vocabulary_ids, known_grammar)][:limit]


def preferred_sentences(text, pronunciation, available_words, limit=3):
    return [[token["text"] for token in entry["tokens"]]
            for entry in preferred_entries(text, pronunciation, available_words, limit)]


def sentence_examples(text, pronunciation, known_words, highlighted_characters, limit=3, *,
                      vocabulary=None, known_vocabulary_ids=frozenset(), known_grammar=frozenset()):
    result = []
    for entry in preferred_entries(text, pronunciation, known_words, limit,
                                   vocabulary=vocabulary,
                                   available_vocabulary_ids=known_vocabulary_ids, known_grammar=known_grammar):
        result.append(
            {
                "languageTag": "cmn-Hans",
                "text": "".join(token["text"] for token in entry["tokens"]),
                "pinyin": entry["pinyin"],
                "audio": audio_reference("".join(token["text"] for token in entry["tokens"]), entry["pinyin"],
                    word_texts=[token['text'] for token in entry['tokens'] if token.get('pinyin')]),
                "english": entry["english"],
                "source": entry["source"],
                **({"targetSpan": entry["targetSpan"]} if entry.get("targetSpan") else {}),
                "tokens": [
                    {**token,
                     "meaning": (token['meaning'] if token.get('meaningScope') == 'sentence' else primary_translations(
                         {"en": token.get("meaning", "")}, text=token["text"],
                         pronunciation={"scheme": "pinyin", "value": token.get("pinyin", "")})["en"]
                         if not token.get("vocabularyId") else word_translations(
                             {'en': token.get('meaning', '')}, vocabulary_id=token['vocabularyId'], text=token['text'],
                             pronunciation={'scheme': 'pinyin', 'value': token.get('pinyin', '')},
                             sense_key=_common_index().get(token['vocabularyId'], {}).get('sense_key', ''))['en']),
                     "highlighted": bool(set(token["text"]) & highlighted_characters)}
                    for token in entry["tokens"]
                ],
            }
        )
        if len(result) == limit:
            break
    return result


def example_sentence(text, pronunciation, known_words, highlighted_characters, *, meaning=None,
                     vocabulary=None, known_vocabulary_ids=frozenset(), known_grammar=frozenset()):
    # Missing prerequisites or authored coverage never turn a word/gloss into a
    # fabricated sentence. Common-word callers must supply the lexical identity.
    authored = sentence_examples(text, pronunciation, known_words, highlighted_characters, limit=1,
                                 vocabulary=vocabulary, known_vocabulary_ids=known_vocabulary_ids,
                                 known_grammar=known_grammar)
    return authored[0] if authored else None
