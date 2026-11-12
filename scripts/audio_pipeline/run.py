"""Finite, resumable two-voice production. Only accepted MP3s reach S3."""

import argparse
import hashlib
import importlib.metadata
import json
import logging
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import soundfile as sf
from botocore.config import Config
from botocore.exceptions import ClientError
from scipy.signal import resample_poly

from scripts.audio_pipeline.endings import assess_endings, verify_endings
from scripts.audio_pipeline.profiles import DATA, catalogue, profile, validate_policy
from scripts.audio_pipeline.punctuation import assess_punctuation, verify_punctuation
from scripts.audio_pipeline.quality import POLICY, Quality, chinese, verify_encoded
from scripts.audio_pipeline.synthesis import InvalidGeneration, Synthesizer, input_variants


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def put_immutable(s3, bucket, key, data, content_type):
    sha = hashlib.sha256(data).hexdigest()
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=31536000, immutable",
            Metadata={"sha256": sha},
            IfNoneMatch="*",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        if head["Metadata"].get("sha256") != sha or head["ContentLength"] != len(data):
            raise ValueError("Immutable object conflict; existing audio was preserved") from error


def main():
    p = argparse.ArgumentParser()
    for name in ["source", "weights", "reference", "asr"]:
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--voice", choices=["female", "male"], required=True)
    p.add_argument("--spool", type=Path, default=DATA.parents[2] / ".audio-production")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ids", type=Path)
    p.add_argument("--phonetic", type=Path, required=True,
                   help="Pinned offline phonetic/timing bundle for punctuation qualification")
    p.add_argument("--shards", type=int, default=1)
    p.add_argument("--shard", type=int, default=0)
    args = p.parse_args()
    if not 0 <= args.shard < args.shards:
        p.error("Invalid shard")
    recipe, version = profile(args.voice)
    validate_policy(recipe)
    asr_revision = (
        (args.asr / ".cache/huggingface/download/model.pt.metadata").read_text().splitlines()[0]
    )
    if asr_revision != POLICY["asrRevision"]:
        raise ValueError("ASR revision mismatch")
    storage = json.loads((DATA / "storage.json").read_text())
    s3 = boto3.client(
        "s3",
        region_name=storage["Region"],
        config=Config(retries={"mode": "adaptive", "total_max_attempts": 8}),
    )
    prefix = f"sentences/{version}/"
    uploaded = {
        r["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=storage["Bucket"], Prefix=prefix
        )
        for r in page.get("Contents", [])
    }
    order, entries = catalogue()
    selected = set(json.loads(args.ids.read_text())) if args.ids else set(order)
    if not selected <= set(order):
        p.error("Unknown content ID")
    pending = [
        i
        for i in order
        if i in selected
        and int(i[:8], 16) % args.shards == args.shard
        and not (prefix + i + ".json" in uploaded and prefix + i + ".mp3" in uploaded)
    ]
    if args.limit:
        pending = pending[: args.limit]
    root = args.spool / version
    root.mkdir(parents=True, exist_ok=True)
    synth = Synthesizer(args.source, args.weights, recipe, args.reference)
    qa = Quality(args.asr, phonetic_dir=args.phonetic)
    logging.getLogger().setLevel(logging.ERROR)
    started = time.monotonic()
    accepted = failed = 0
    for index, audio_id in enumerate(pending):
        entry = entries[audio_id]
        manifest_file = root / (audio_id + ".json")
        mp3_file = root / (audio_id + ".mp3")
        attempts = []
        item_started = time.monotonic()
        try:
            if manifest_file.exists() and mp3_file.exists():
                manifest = json.loads(manifest_file.read_text())
                data = mp3_file.read_bytes()
                if (
                    manifest["profile"] != recipe
                    or manifest["id"] != audio_id
                    or manifest["sha256"] != hashlib.sha256(data).hexdigest()
                    or not manifest["quality"]["accepted"]
                ):
                    raise ValueError("Invalid resumable receipt")
                decoded, rate = sf.read(mp3_file, dtype="float32")
                if verify_punctuation(decoded, rate, entry["text"], entry["pinyin"],
                                      manifest["quality"].get("punctuation")):
                    raise ValueError("Resumable receipt fails punctuation qualification")
                if verify_endings(decoded, rate, entry["text"], entry["pinyin"],
                                  manifest["quality"].get("endings")):
                    raise ValueError("Resumable receipt fails ending-noise qualification")
            else:
                manifest = None
                rejection = root / (audio_id + ".rejected.json")
                if rejection.exists():
                    saved = json.loads(rejection.read_text())
                    if saved["id"] != audio_id or saved["text"] != entry["text"]:
                        raise ValueError("Rejected-attempt receipt mismatch")
                    attempts = saved["attempts"]
                variants = input_variants(entry)
                for attempt in range(len(attempts), POLICY["maxAttempts"]):
                    seed = recipe["seed"] + attempt
                    primary = "instruct2" if len(chinese(entry["text"])) <= 3 else "zero_shot"
                    modes = [
                        primary,
                        "zero_shot" if primary == "instruct2" else "instruct2",
                        "clear",
                    ]
                    modes = recipe.get("retryModes", modes)
                    repetitions = recipe.get("seedsPerInputVariant", 1)
                    if (
                        not modes
                        or any(
                            mode not in {"zero_shot", "instruct2", "clear", "cross_lingual"}
                            for mode in modes
                        )
                        or repetitions not in {1, 2, 3}
                    ):
                        raise ValueError("Invalid bounded retry recipe")
                    variant = variants[(attempt // (len(modes) * repetitions)) % len(variants)]
                    native = variant in {"native", "unquoted-native"}
                    romanized = variant == "romanized-neutral"
                    mode = modes[attempt % len(modes)]
                    synth.set_mode(mode, entry)
                    try:
                        raw, termination, input_text, overrides = synth.generate(
                            entry, seed, variant=variant
                        )
                    except InvalidGeneration as error:
                        attempts.append(
                            {
                                "seed": seed,
                                "mode": mode,
                                "inputVariant": variant,
                                "accepted": False,
                                "failures": ["synthesis-too-short"],
                                "termination": dict(synth.trace),
                                "error": str(error),
                            }
                        )
                        atomic_json(rejection, {"id": audio_id, **entry, "attempts": attempts})
                        continue
                    finished, quality = qa.assess(
                        raw, synth.sample_rate, entry["text"], termination, pinyin=entry["pinyin"]
                    )
                    if quality["accepted"]:
                        with tempfile.TemporaryDirectory(prefix="heyhanzi-audio-") as temporary:
                            wav = Path(temporary) / "working.wav"
                            mp3 = Path(temporary) / "delivery.mp3"
                            sf.write(wav, finished, synth.sample_rate, subtype="PCM_24")
                            subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-v",
                                    "error",
                                    "-i",
                                    str(wav),
                                    "-map_metadata",
                                    "-1",
                                    "-codec:a",
                                    "libmp3lame",
                                    "-b:a",
                                    "96k",
                                    "-ar",
                                    "24000",
                                    "-ac",
                                    "1",
                                    str(mp3),
                                ],
                                check=True,
                            )
                            data = mp3.read_bytes()
                            delivered, delivered_rate = sf.read(mp3, dtype="float32")
                            if delivered_rate != 24000:
                                raise ValueError("Unexpected delivery sample rate")
                            evidence = qa.timing_evidence(resample_poly(delivered, 2, 3),
                                                          entry["text"], entry["pinyin"])
                            quality["punctuation"] = assess_punctuation(
                                delivered, 24000, entry["text"], entry["pinyin"], evidence)
                            quality["endings"] = assess_endings(
                                delivered, 24000, entry["text"], entry["pinyin"], evidence)
                            quality["failures"] += verify_encoded(delivered)
                            quality["failures"] += verify_endings(
                                delivered, 24000, entry["text"], entry["pinyin"],
                                quality.get("endings"),
                            )
                            quality["failures"] += verify_punctuation(
                                delivered, 24000, entry["text"], entry["pinyin"],
                                quality.get("punctuation"),
                            )
                            if not 0 < len(data) <= 2_000_000:
                                quality["failures"].append("delivery-size")
                            quality["accepted"] = not quality["failures"]
                    attempts.append(
                        {"seed": seed, "mode": mode, "inputVariant": variant, **quality,
                         # Keep boundary measurements for every failure, and full
                         # timing tokens once on the accepted quality receipt.
                         "punctuation": {k: v for k, v in quality.get("punctuation", {}).items()
                                         if k != "timingEvidence"},
                         "endings": {k: v for k, v in quality.get("endings", {}).items()
                                     if k != "timingEvidence"}}
                    )
                    if not quality["accepted"]:
                        atomic_json(rejection, {"id": audio_id, **entry, "attempts": attempts})
                    if quality["accepted"]:
                        manifest = {
                            "id": audio_id,
                            **entry,
                            "voiceId": recipe["voiceId"],
                            "voiceVersion": version,
                            "profile": recipe,
                            "input": input_text,
                            "pronunciationOverrides": overrides,
                            "seed": seed,
                            "mode": mode,
                            "inputVariant": variant,
                            "instructionPrompt": synth.prompt
                            if mode in {"instruct2", "clear"}
                            else None,
                            "nativeInput": native,
                            "romanizedInput": romanized,
                            "quality": quality,
                            "attempts": attempts,
                            "reviewStatus": "automated-pass",
                            "humanReviewed": False,
                            "compute": {
                                name: importlib.metadata.version(name)
                                for name in [
                                    "torch",
                                    "numpy",
                                    "funasr",
                                    "silero-vad",
                                    "onnxruntime",
                                    "pypinyin",
                                ]
                            },
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "byteLength": len(data),
                            "durationSeconds": quality["durationSeconds"],
                            "generatedAt": datetime.now(timezone.utc).isoformat(),
                        }
                        temp = mp3_file.with_suffix(".tmp")
                        temp.write_bytes(data)
                        temp.replace(mp3_file)
                        atomic_json(manifest_file, manifest)
                        break
                if manifest is None:
                    failed += 1
                    atomic_json(
                        root / (audio_id + ".rejected.json"),
                        {"id": audio_id, **entry, "attempts": attempts},
                    )
                    print(
                        json.dumps(
                            {
                                "event": "rejected",
                                "voice": args.voice,
                                "id": audio_id,
                                "text": entry["text"],
                                "reasons": attempts[-1]["failures"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    continue
            # Receipt + MP3 survive any interrupted upload. Metadata is the commit marker.
            put_immutable(s3, storage["Bucket"], prefix + audio_id + ".mp3", data, "audio/mpeg")
            put_immutable(
                s3,
                storage["Bucket"],
                prefix + audio_id + ".json",
                json.dumps(manifest, ensure_ascii=False).encode(),
                "application/json",
            )
            accepted += 1
            (root / (audio_id + ".rejected.json")).unlink(missing_ok=True)
            print(
                json.dumps(
                    {
                        "event": "accepted",
                        "voice": args.voice,
                        "id": audio_id,
                        "text": entry["text"],
                        "attempts": len(manifest["attempts"]),
                        "duration": manifest["durationSeconds"],
                        "seconds": round(time.monotonic() - item_started, 2),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as error:
            failed += 1
            print(
                json.dumps(
                    {
                        "event": "failed",
                        "voice": args.voice,
                        "id": audio_id,
                        "error": str(error)[:300],
                    }
                ),
                flush=True,
            )
        finally:
            elapsed = time.monotonic() - started
            done = index + 1
            atomic_json(
                root / f"status-{args.shard}.json",
                {
                    "voice": args.voice,
                    "voiceVersion": version,
                    "expected": len(pending),
                    "processed": done,
                    "accepted": accepted,
                    "failed": failed,
                    "elapsedSeconds": elapsed,
                    "etaSeconds": elapsed / done * (len(pending) - done),
                    "complete": done == len(pending) and failed == 0,
                },
            )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
