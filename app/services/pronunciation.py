"""Immutable, pre-generated pronunciation assets, independent of user state.

The initial asset ships with the backend; no model runs in the request process.
The reference contract also permits moving this storage boundary to a CDN later.
"""
import json
from pathlib import Path

from fastapi import HTTPException

from app.schemas.learning import AudioReference

ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "data/pronunciation"
# Explicit catalog: never resolve an arbitrary user-supplied filename.
CATALOG = [
    AudioReference.model_validate(json.loads((ASSET_DIRECTORY / manifest).read_text())["reference"])
    for manifest in ("wo-wo3.json", "wo-wo3-adult-slow.json", "wo-wo3-faster.json", "wo-wo3-young-adult.json",
                     "ni-ni3-young-adult.json", "hao-hao3-young-adult.json")
]


def audio_file(language_tag, audio_id, version):
    for reference in CATALOG:
        if (reference.languageTag, reference.id, reference.version) == (language_tag, audio_id, version):
            return ASSET_DIRECTORY / f"{reference.version}.mp3"
    raise HTTPException(404, detail={"code": "learning_audio_not_found"})
