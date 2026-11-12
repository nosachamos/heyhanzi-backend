"""Fetch pinned QA models and reference-only samples into an external directory."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

from huggingface_hub import snapshot_download

from scripts.audio_pipeline.phonetic import PHONETIC_POLICY, PhoneticRecognizer
from scripts.audio_pipeline.profiles import profile
from scripts.audio_pipeline.quality import POLICY


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, required=True)
    args = p.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        POLICY["asr"],
        revision=POLICY["asrRevision"],
        local_dir=str(args.directory / "asr"),
        ignore_patterns=["*.onnx", "*.bin"],
    )
    url = (
        "https://raw.githubusercontent.com/microsoft/DNS-Challenge/"
        + POLICY["dnsmosRevision"]
        + "/DNSMOS/DNSMOS/sig_bak_ovr.onnx"
    )
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != POLICY["dnsmosSha256"]:
        raise ValueError("DNSMOS checksum mismatch")
    (args.directory / "asr/sig_bak_ovr.onnx").write_bytes(data)
    phonetic = args.directory / "phonetic"
    snapshot_download(
        PHONETIC_POLICY["model"],
        revision=PHONETIC_POLICY["modelRevision"],
        local_dir=str(phonetic / "model"),
        allow_patterns=["checkpoint-epoch=0009.ckpt", "tokens.txt", "README.md", "LICENSE*"],
    )
    snapshot_download(
        PHONETIC_POLICY["source"],
        repo_type="space",
        revision=PHONETIC_POLICY["sourceRevision"],
        local_dir=str(phonetic / "source"),
        allow_patterns=["whisper/**", "README.md", "LICENSE*"],
    )
    # Verify source, tokens, weights and strict architecture compatibility now,
    # before launching a finite production job. Never run the upstream demo app.
    PhoneticRecognizer(phonetic)
    for voice in ["female", "male"]:
        recipe, _ = profile(voice)
        ref = recipe["referenceVoice"]
        data = urllib.request.urlopen(ref["sourceUrl"], timeout=60).read()
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError("Reference checksum mismatch")
        (args.directory / ref["file"]).write_bytes(data)
    print(json.dumps({"prepared": str(args.directory)}))


if __name__ == "__main__":
    main()
