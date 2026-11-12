"""Trusted, immutable authored content; no learner data in the content cache."""

import hashlib
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from app.services import course_media, course_sets, course_story
from app.services.learning import learning_error

CONTENT = Path(__file__).resolve().parents[1] / "data/learning/course-index.json"
TRACK_ID = "mandarin-simplified-v1"


class LexicalRequirements(Mapping):
    """Immutable content references; never cache personal eligibility."""

    def __init__(self, references):
        self.references = references

    def __iter__(self):
        return iter(self.references)

    def __len__(self):
        return len(self.references)

    def __getitem__(self, identifier):
        ref = self.references[identifier]
        return _read_requirements(ref['path'], ref['sha256'])


@lru_cache(maxsize=16)
def _read_requirements(relative, sha):
    if not re.fullmatch('[a-f0-9]{64}', sha) or relative != f'course-requirements/{sha}.json':
        raise ValueError('Invalid lexical requirement reference')
    body = (CONTENT.parent / relative).read_bytes()
    if hashlib.sha256(body).hexdigest() != sha:
        raise ValueError('Authored lexical requirements changed')
    value = json.loads(body)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError('Invalid lexical requirements')
    return value


@lru_cache(maxsize=1)
def _read_catalog(modified, size):
    data = json.loads(CONTENT.read_text())
    if data.get("format_version") != 1 or data.get("course_id") != "COURSE-MANDARIN-CORE":
        raise ValueError("Unsupported authored course manifest")
    by_id = {}
    sharded = data.get("definition_storage") == "sharded-v1"
    if sharded:
        claimed = data.get("indexSha256")
        actual = hashlib.sha256(
            json.dumps(
                {k: v for k, v in data.items() if k != "indexSha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if claimed != actual:
            raise ValueError("Invalid authored course index fingerprint")
    for item in data["exercises"]:
        if sharded:
            reference = item.get("definition_ref", {})
            sha = reference.get("sha256", "")
            if (
                item["id"] in by_id
                or not re.fullmatch("[a-f0-9]{64}", sha)
                or reference.get("path") != f"course-occurrences/{sha}.json"
            ):
                raise ValueError("Invalid lazy authored occurrence reference")
            by_id[item["id"]] = item
            continue
        source = {key: value for key, value in item.items() if key != "fingerprint"}
        actual = hashlib.sha256(
            json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if item.get("fingerprint") != actual or item["id"] in by_id:
            raise ValueError("Invalid authored course fingerprint or duplicate ID")
        if course_sets.is_set(item):
            course_sets.validate_definition(item)
            by_id[item["id"]] = item
            continue
        if course_story.is_story(item):
            course_story.validate_definition(item)
            by_id[item["id"]] = item
            continue
        steps = item["definition"]["steps"]
        if len(steps) != 1 or steps[0]["kind"] != "check" or len(steps[0]["questions"]) != 1:
            raise ValueError("Unsupported course exercise shape")
        question = steps[0]["questions"][0]
        ids = [tile["id"] for tile in question["tiles"]]
        if question["kind"] != "cloze" or len(ids) != len(set(ids)) or not question["solutions"]:
            raise ValueError("Unsupported course question")
        if any(
            not answer or len(answer) != len(set(answer)) or not set(answer) <= set(ids)
            for answer in question["solutions"]
        ):
            raise ValueError("Invalid authored solution")
        by_id[item["id"]] = item
    compatibility_rows = dict(by_id)
    if sharded:
        for identifier in (data.get("media_compatibility") or {}).get("items", {}):
            compact = by_id.get(identifier)
            if compact:
                compatibility_rows[identifier] = _read_occurrence(
                    compact["definition_ref"]["path"], compact["definition_ref"]["sha256"]
                )
    course_media.validate_compatibility(data, compatibility_rows)
    policy = data.get("learner_policy")
    if policy is not None:
        optional_ids = policy.get("optional_exercise_ids")
        if (
            policy.get("format_version") != 1
            or not isinstance(optional_ids, list)
            or any(not isinstance(value, str) for value in optional_ids)
            or len(set(optional_ids)) != len(optional_ids)
            or not set(optional_ids) <= by_id.keys()
        ):
            raise ValueError("Invalid learner exercise policy")
        if 'lexical_requirement_refs' in data:
            refs = data['lexical_requirement_refs']
            if (not sharded or policy.get('lexical_requirements') != {} or not isinstance(refs, dict)
                    or set(refs) != set(by_id) or any(not isinstance(ref, dict)
                    or not re.fullmatch('[a-f0-9]{64}', str(ref.get('sha256', '')))
                    or ref.get('path') != f"course-requirements/{ref.get('sha256')}.json" for ref in refs.values())):
                raise ValueError('Invalid lexical requirement index')
            policy['lexical_requirements'] = LexicalRequirements(refs)
    return data, by_id


def catalog():
    try:
        stat = CONTENT.stat()
    except FileNotFoundError:
        raise learning_error(503, "course_content_unavailable") from None
    return _read_catalog(stat.st_mtime_ns, stat.st_size)


def exercise(instance_id):
    item = catalog()[1].get(instance_id)
    if item is None:
        raise learning_error(404, "course_exercise_not_found")
    reference = item.get("definition_ref")
    if reference:
        full = _read_occurrence(reference["path"], reference["sha256"])
        if any(
            full.get(key) != item.get(key)
            for key in ("id", "revision", "fingerprint", "activity", "unit", "after")
        ):
            raise ValueError("Authored course index does not match occurrence")
        return full
    return item


@lru_cache(maxsize=16)
def _read_occurrence(relative, sha):
    if relative != f"course-occurrences/{sha}.json" or not re.fullmatch("[a-f0-9]{64}", sha):
        raise ValueError("Invalid authored course reference")
    body = (CONTENT.parent / relative).read_bytes()
    if hashlib.sha256(body).hexdigest() != sha:
        raise ValueError("Authored occurrence bytes changed")
    item = json.loads(body)
    source = {key: value for key, value in item.items() if key != "fingerprint"}
    actual = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if item.get("fingerprint") != actual:
        raise ValueError("Invalid authored occurrence fingerprint")
    if course_sets.is_set(item):
        course_sets.validate_definition(item)
    elif course_story.is_story(item):
        course_story.validate_definition(item)
    else:
        steps = item["definition"]["steps"]
        if (
            len(steps) != 1
            or steps[0]["kind"] != "check"
            or len(steps[0]["questions"]) != 1
            or steps[0]["questions"][0]["kind"] != "cloze"
        ):
            raise ValueError("Unsupported authored occurrence")
        q = steps[0]["questions"][0]
        ids = {tile["id"] for tile in q["tiles"]}
        if (
            len(ids) != len(q["tiles"])
            or not q["solutions"]
            or any(not s or len(s) != len(set(s)) or not set(s) <= ids for s in q["solutions"])
        ):
            raise ValueError("Invalid authored sentence solution")
    return item


def is_optional(item):
    # The legacy item flag is fingerprinted. Learner placement policy changes
    # independently, preserving saved answers when optionality is clarified.
    policy = catalog()[0].get("learner_policy")
    return (
        item["id"] in policy["optional_exercise_ids"]
        if policy is not None
        else item.get("optional", False)
    )


def current_content(item, revision, fingerprint):
    if not matches_content(item, revision, fingerprint):
        raise learning_error(409, "course_content_changed")


def accepted_fingerprints(item):
    row = (catalog()[0].get("media_compatibility") or {}).get("items", {}).get(item["id"], {})
    return (item["fingerprint"], *row.get("previousFingerprints", []))


def matches_content(item, revision, fingerprint):
    return item["revision"] == revision and fingerprint in accepted_fingerprints(item)


def check_tiles(item, answer):
    question = item["definition"]["steps"][0]["questions"][0]
    tiles = {tile["id"]: tile for tile in question["tiles"]}
    if len(answer) != len(set(answer)) or not set(answer) <= tiles.keys():
        raise learning_error(422, "course_invalid_tiles")
    return question, tiles


def grade(item, answer):
    question, tiles = check_tiles(item, answer)

    # Identical copies are interchangeable; homographic readings are not.
    def values(sequence):
        return [(tiles[key]["text"], tiles[key].get("pinyin")) for key in sequence]

    correct = bool(answer) and any(
        values(answer) == values(solution) for solution in question["solutions"]
    )
    return correct, question["feedback"]["correct" if correct else "incorrect"]
