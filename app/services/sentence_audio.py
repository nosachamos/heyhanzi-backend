"""Pre-generated sentence references, delivered directly through the audio CDN.

Only curriculum IDs can be requested. No browser text, user data, or model runs
are accepted by this boundary. Storage clients belong to the app instance.
"""

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.services.audio_qualification import release_is_qualified

DIRECTORY = Path(__file__).resolve().parents[1] / "data/sentence-audio"
PROFILE = json.loads((DIRECTORY / "voice.json").read_text(encoding="utf-8"))
VOICE_VERSION = hashlib.sha256(
    json.dumps(PROFILE, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
COURSE_AUDIO = DIRECTORY.parent / "learning/course-audio"
COURSE_POLICY = "current-course-notebook-audio-v1"
COURSE_PROFILES = {
    "female": "47a4e2b453fd35ad71fdfe8bcd1739bb1b4abe41fa6ce3475ec883bf112586b1",
    "male": "d74a42ec22281980e681667b3869f5bb87a8d01dea370f1c910d832f8f44a08f",
}


def sentence_id(text, pinyin):
    return hashlib.sha256(
        json.dumps(["cmn-Hans", text, " ".join(pinyin.split())], ensure_ascii=False).encode()
    ).hexdigest()


def _course_reading(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value).casefold())


def _course_identity(text, pinyin):
    return hashlib.sha256(
        json.dumps(
            ["cmn-Hans", text, _course_reading(pinyin)], ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _authored_reading(value):
    # Used only to verify an explicitly exported authored-request alias. Runtime
    # lookup itself keeps punctuation/apostrophes and never changes a tone.
    return re.sub(r"[\s'’.,。！？!?，；:：;·-]", "", unicodedata.normalize("NFC", value).casefold())


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _course_unavailable():
    return HTTPException(503, detail={"code": "sentence_audio_unavailable"})


@lru_cache(maxsize=1)
def _course_index(modified, size):
    if not 0 < size <= 262144:
        raise _course_unavailable()
    try:
        document = json.loads((COURSE_AUDIO / "index.json").read_text())
        if (
            not isinstance(document, dict)
            or document.get("format_version") != 1
            or document.get("policy") != COURSE_POLICY
            or document.get("profiles") != COURSE_PROFILES
            or document.get("indexSha256")
            != _digest({k: v for k, v in document.items() if k != "indexSha256"})
            or not all(
                re.fullmatch("[a-f0-9]{64}", document.get(field, ""))
                for field in ["sourceAudioSha256", "requestProjectionSha256"]
            )
            or not isinstance(document.get("shards"), dict)
            or len(document["shards"]) > 256
        ):
            raise ValueError("Invalid current notebook audio manifest")
        count = 0
        for prefix, row in document["shards"].items():
            if (
                not isinstance(row, dict)
                or not re.fullmatch("[a-f0-9]{2}", prefix)
                or not re.fullmatch("[a-f0-9]{64}", row.get("sha256", ""))
                or row.get("path") != prefix + "-" + row["sha256"] + ".json"
                or type(row.get("byteLength")) is not int
                or not 0 < row["byteLength"] <= 2_097_152
                or type(row.get("entries")) is not int
                or not 0 < row["entries"] <= 4096
            ):
                raise ValueError("Invalid current notebook audio shard reference")
            count += row["entries"]
        if type(document.get("entries")) is not int or document["entries"] != count:
            raise ValueError("Invalid current notebook audio count")
        return document
    except (OSError, ValueError, TypeError, KeyError):
        raise _course_unavailable() from None


def _validate_course_row(key, row):
    from app.schemas.learning import AudioReference

    if (
        not isinstance(row, dict)
        or not isinstance(row.get("text"), str)
        or not 0 < len(row["text"]) <= 4000
        or not isinstance(row.get("pinyin"), str)
        or not 0 < len(row["pinyin"]) <= 4000
        or key != _course_identity(row["text"], row["pinyin"])
        or row.get("reading") != _course_reading(row["pinyin"])
        or row.get("profileVersions") != COURSE_PROFILES
        or not all(
            re.fullmatch("[a-f0-9]{64}", row.get(field, ""))
            for field in ["sourceAudioKey", "sourceEntrySha256"]
        )
        or set(row.get("voices", {})) != {"mandarin-female", "mandarin-male"}
    ):
        raise ValueError("Invalid current notebook audio identity")
    alias = row.get("alias")
    if "alias" in row:
        if (
            not isinstance(alias, dict)
            or alias.get("sourceAudioKey") != row["sourceAudioKey"]
            or alias.get("sourceEntrySha256") != row["sourceEntrySha256"]
            or not isinstance(alias.get("sourcePinyin"), str)
        ):
            raise ValueError("Unbound current notebook audio alias")
        if alias.get("policy") == "explicit-isolated-word-terminal-full-stop-v1":
            same_reading = _course_reading(row["pinyin"]) == _course_reading(alias["sourcePinyin"])
            authored_format = row.get("requestId") == key and _authored_reading(
                row["pinyin"]
            ) == _authored_reading(alias["sourcePinyin"])
            if (
                not re.fullmatch(r"[\u3400-\u9fff]+", alias.get("sourceText", ""))
                or row["text"] != alias["sourceText"] + "。"
                or not (same_reading or authored_format)
            ):
                raise ValueError("Invalid isolated-word terminal-stop alias")
        elif (
            alias.get("policy") != "authored-notebook-reading-format-v1"
            or row["text"] != alias.get("sourceText")
            or row.get("requestId") != key
            or _authored_reading(row["pinyin"]) != _authored_reading(alias["sourcePinyin"])
        ):
            raise ValueError("Invalid authored notebook request alias")
    for voice, profile in COURSE_PROFILES.items():
        value = row["voices"]["mandarin-" + voice]
        reference = AudioReference.model_validate(value)
        if (
            reference.voiceId != "mandarin-" + voice
            or reference.languageTag != "cmn-Hans"
            or reference.id != "sentence-" + row["sourceAudioKey"]
            or reference.voices
            or not reference.url
        ):
            raise ValueError("Invalid current notebook audio voice")
        url = urlsplit(reference.url)
        relative = re.fullmatch(r"/exercise-assets/audio/[a-zA-Z0-9_-]+\.mp3", reference.url)
        remote = (
            url.scheme == "https"
            and url.netloc == "dzxcg0bfut03j.cloudfront.net"
            and url.path.startswith("/sentences/" + profile + "/")
            and not url.query
            and not url.fragment
        )
        if not relative and not remote:
            raise ValueError("Current notebook audio URL is outside its bound delivery")


@lru_cache(maxsize=16)
def _course_shard(prefix, name, sha, size, entries):
    try:
        path = COURSE_AUDIO / name
        if path.stat().st_size != size:
            raise ValueError("Changed current notebook audio shard size")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("Changed current notebook audio shard")
        body = json.loads(raw)
        if (
            not isinstance(body, dict)
            or body.get("format_version") != 1
            or body.get("policy") != COURSE_POLICY
            or body.get("prefix") != prefix
            or not isinstance(body.get("items"), dict)
            or len(body["items"]) != entries
        ):
            raise ValueError("Invalid current notebook audio shard")
        for key, row in body["items"].items():
            if not re.fullmatch(prefix + "[a-f0-9]{62}", key):
                raise ValueError("Misplaced notebook audio identity")
            _validate_course_row(key, row)
        return body["items"]
    except (OSError, ValueError, TypeError, KeyError):
        raise _course_unavailable() from None


def _course_reference(text, pinyin):
    try:
        stat = (COURSE_AUDIO / "index.json").stat()
    except FileNotFoundError:
        if COURSE_AUDIO.exists():
            raise _course_unavailable() from None
        return None
    except OSError:
        raise _course_unavailable() from None
    index = _course_index(stat.st_mtime_ns, stat.st_size)
    key = _course_identity(text, pinyin)
    prefix = key[:2]
    shard = index["shards"].get(prefix)
    if shard is None:
        return None
    row = _course_shard(
        prefix, shard["path"], shard["sha256"], shard["byteLength"], shard["entries"]
    ).get(key)
    if row is None:
        return None
    return {**row["voices"]["mandarin-female"], "voices": row["voices"]}


ACTIVE_FILE = DIRECTORY / "active.json"
ACTIVE_RELEASE = json.loads(ACTIVE_FILE.read_text())["releaseId"] if ACTIVE_FILE.exists() else None
if ACTIVE_RELEASE is not None and not re.fullmatch(r"[0-9a-f]{64}", ACTIVE_RELEASE):
    raise ValueError("Invalid audio release ID")
if ACTIVE_RELEASE:
    release_directory = DIRECTORY / "releases" / ACTIVE_RELEASE
    release = json.loads((release_directory / "release.json").read_text())
    if (
        release.get("releaseId") != ACTIVE_RELEASE
        or not release_is_qualified(release)
        or any(not (release_directory / f"{prefix:02x}.json").is_file() for prefix in range(256))
    ):
        raise ValueError("Incomplete active audio release")


def current_reference(text, pinyin, *, word_texts=None):
    """Exact current pinned voices, with no historical release fallback."""
    current = _course_reference(text, pinyin)
    return _with_read_along(current, text, word_texts) if current is not None else None


def reference(text, pinyin, *, word_texts=None):
    current = current_reference(text, pinyin, word_texts=word_texts)
    if current is not None:
        return current
    audio_id = sentence_id(text, pinyin)
    published = _published_shard(audio_id[:2]).get(audio_id)
    return _with_read_along(published, text, word_texts) if published else None


@lru_cache(maxsize=64)
def _read_along(sha):
    """Bounded static authoring data; never derive timings in the API worker."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("Invalid read-along recording SHA")
    path = DIRECTORY.parent / "learning/read-along" / (sha + ".json")
    if not path.is_file():
        return ()
    if path.stat().st_size > 131072:
        raise ValueError("Read-along sidecar exceeds its bounded budget")
    document = json.loads(path.read_text())
    variants = document.get("variants")
    if (
        document.get("format_version") != 1
        or document.get("audioSha256") != sha
        or not isinstance(variants, list)
        or not 1 <= len(variants) <= 32
    ):
        raise ValueError("Invalid recording-bound read-along sidecar")
    from app.schemas.learning import ReadAlong

    return tuple(
        ReadAlong(audioSha256=sha, **variant).model_dump(exclude_none=True) for variant in variants
    )


def _with_read_along(published, text, word_texts=None):
    result = dict(published)
    timing = next(
        (
            value
            for value in _read_along(published["version"])
            if value["text"] == text
            and (
                word_texts is None
                or [word["text"] for word in value["words"] if "start" in word] == word_texts
            )
        ),
        None,
    )
    if timing:
        result["readAlong"] = timing
    if published.get("voices"):
        result["voices"] = {
            voice: _with_read_along(value, text, word_texts)
            for voice, value in published["voices"].items()
        }
    return result


def pronunciation(text, original):
    """Overlay published variants without mutating stored curriculum metadata."""
    if original.get("scheme") != "pinyin":
        return original
    from app.services.primary_character_contexts import uses_word_context

    if uses_word_context(text, original):
        # The full introduced word has its own visible label and playback in
        # Common words. Never put that recording under an isolated glyph.
        return {key: value for key, value in original.items() if key != "audio"}
    audio = (
        reference(text + "。", original["value"], word_texts=[text])
        if ACTIVE_RELEASE
        else _course_reference(text + "。", original["value"])
    )
    if audio and not ACTIVE_RELEASE:
        audio = _with_read_along(audio, text + "。", [text])
    return {**original, "audio": audio} if audio else original


@lru_cache(maxsize=16)
def _published_shard(prefix):
    file = (
        DIRECTORY / "releases" / ACTIVE_RELEASE / f"{prefix}.json"
        if ACTIVE_RELEASE
        else DIRECTORY / "published" / VOICE_VERSION / f"{prefix}.json"
    )
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


@lru_cache(maxsize=16)
def _shard(prefix):
    file = DIRECTORY / "catalogue" / f"{prefix}.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


def catalogue_entry(audio_id, voice_version):
    if voice_version != VOICE_VERSION or not re.fullmatch(r"[0-9a-f]{64}", audio_id):
        raise HTTPException(404, detail={"code": "sentence_audio_not_found"})
    entry = _shard(audio_id[:2]).get(audio_id)
    if entry is None:
        raise HTTPException(404, detail={"code": "sentence_audio_not_found"})
    return entry


def object_key(audio_id):
    return f"sentences/{VOICE_VERSION}/{audio_id}.mp3"
