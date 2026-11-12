"""Small immutable recipes, shared by offline tools only."""

import hashlib
import json
from pathlib import Path

from scripts.audio_pipeline.quality import POLICY
from scripts.audio_pipeline.synthesis import digest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "app/data/sentence-audio"


def profile(voice):
    value = json.loads((DATA / "voices" / f"{voice}.json").read_text())
    return value, digest(value)


def catalogue():
    order = json.loads((DATA / "generation-order.json").read_text())
    entries = {}
    for path in sorted((DATA / "catalogue").glob("*.json")):
        entries.update(json.loads(path.read_text()))
    if len(set(order)) != len(order) or set(order) != set(entries):
        raise ValueError("Recording catalogue and generation order differ")
    # The existing isolated characters also occur in the vocabulary catalogue;
    # reuse their content ID instead of generating duplicate speech assets.
    return order, entries


def validate_policy(value):
    if value.get("pipelineCodeSha256") != implementation_hash():
        raise ValueError(
            "Production code changed; calibrate and version the recipe before generation"
        )
    if value["qualityPolicy"] != POLICY:
        raise ValueError(
            "Recipe quality policy differs from executable policy; create a new version"
        )


def implementation_hash():
    root = Path(__file__).parent
    return hashlib.sha256(
        b"".join(
            (root / name).read_bytes()
            for name in [
                "quality.py",
                "phonetic.py",
                "timing.py",
                "phonetic-model.json",
                "punctuation.py",
                "endings.py",
                "synthesis.py",
                "run.py",
                "pilot.py",
                "publish.py",
                "check.py",
                "profiles.py",
                "../generate_sentence_audio.py",
            ]
        )
    ).hexdigest()
