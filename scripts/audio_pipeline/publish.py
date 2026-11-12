"""Verify every selected MP3 before preparing an immutable, complete release."""

import argparse
import hashlib
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import soundfile as sf
from botocore.config import Config

from app.services.audio_qualification import recording_approval
from scripts.audio_pipeline.endings import verify_endings
from scripts.audio_pipeline.phonetic import phonetic_review_matches
from scripts.audio_pipeline.profiles import DATA, catalogue, profile, validate_policy
from scripts.audio_pipeline.punctuation import verify_punctuation
from scripts.audio_pipeline.quality import POLICY, chinese, transcript_matches, verify_encoded
from scripts.audio_pipeline.run import atomic_json
from scripts.audio_pipeline.synthesis import digest


def has_forced_sandhi_prefix(manifest):
    """Older input controls forced citation tones inside 一/不 compounds."""
    return any(
        len(hint.get("text", "")) > 1
        and any(
            hint["text"].startswith(character) and hint.get("tokens", "").startswith(tokens)
            for character, tokens in [("一", "[y][ī]"), ("不", "[b][ù]")]
        )
        for hint in manifest.get("pronunciationOverrides", [])
    )


def verify_recording(audio_id, entry, recipe, version, manifest, data):
    """Check delivered bytes and persisted qualification against the current catalogue."""
    approval = recording_approval(manifest)
    if (
        manifest["id"] != audio_id
        or manifest["voiceId"] != recipe["voiceId"]
        or manifest["profile"] != recipe
        or manifest["voiceVersion"] != version
        or manifest["text"] != entry["text"]
        or manifest["pinyin"] != entry["pinyin"]
        or manifest["sha256"] != hashlib.sha256(data).hexdigest()
        or manifest["byteLength"] != len(data)
        or not 0 < len(data) <= 2_000_000
        or (not approval and not manifest["quality"]["accepted"])
        or (not approval and manifest["quality"]["failures"])
        or manifest["quality"]["termination"]["reason"] != "eos"
        or (not approval and manifest["reviewStatus"] != "automated-pass")
        or has_forced_sandhi_prefix(manifest)
    ):
        raise ValueError(f"Unqualified recording: {recipe['voiceId']}/{audio_id}")
    samples, sample_rate = sf.read(io.BytesIO(data), dtype="float32")
    quality = manifest["quality"]
    count = len(chinese(entry["text"]))
    lower, upper = POLICY["singleCharacterSeconds"] if count <= 1 else POLICY["secondsPerCharacter"]
    thresholds = (
        POLICY["shortMos"]
        if count <= 3
        else {
            "signal": POLICY["minimumMosSignal"],
            "background": POLICY["minimumMosBackground"],
            "overall": POLICY["minimumMosOverall"],
        }
    )
    if (
        not all(
            math.isfinite(quality["dnsmos"][name]) for name in ["signal", "background", "overall"]
        )
        or not all(
            math.isfinite(value)
            for value in [
                manifest["durationSeconds"],
                quality["durationSeconds"],
                quality["secondsPerCharacter"],
                quality["longestPauseSeconds"],
            ]
        )
        or sample_rate != 24000
        or samples.ndim != 1
        or verify_encoded(samples)
        or verify_endings(samples, sample_rate, entry["text"], entry["pinyin"],
                          quality.get("endings"))
        or verify_punctuation(samples, sample_rate, entry["text"], entry["pinyin"],
                              quality.get("punctuation"))
        or abs(len(samples) / sample_rate - manifest["durationSeconds"]) > 0.025
        or abs(quality["durationSeconds"] - manifest["durationSeconds"]) > 0.025
        or quality["expectedText"] != entry["text"]
        or not (
            approval
            or transcript_matches(entry["text"], quality["transcript"], entry["pinyin"])
            or phonetic_review_matches(entry["text"], entry["pinyin"], quality["transcript"], quality.get("phoneticEvidence"))
        )
        or not lower <= quality["secondsPerCharacter"] <= upper
        or not 0 <= quality["longestPauseSeconds"] <= POLICY["maxPauseSeconds"]
        or any(
            quality["dnsmos"][name] < thresholds[name]
            for name in ["signal", "background", "overall"]
        )
        or quality["termination"]["generatedSpeechTokens"]
        >= quality["termination"]["maximumSpeechTokens"]
    ):
        raise ValueError(f"Audio fails release quality gates: {recipe['voiceId']}/{audio_id}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", "--output-dir", type=Path, required=True)
    p.add_argument(
        "--require-complete",
        action="store_true",
        help="Compatibility flag: completeness is always mandatory",
    )
    p.add_argument(
        "--verify", action="store_true", help="Compatibility flag: every MP3 is always verified"
    )
    p.add_argument("--progress", action="store_true", help="Progress is always reported")
    p.add_argument("--report", type=Path)
    p.add_argument("--threads", type=int, default=8, help="Bounded S3 audit concurrency (1–32)")
    p.add_argument(
        "--activate",
        action="store_true",
        help="Activate complete local metadata; does not deploy the API",
    )
    args = p.parse_args()
    if not 1 <= args.threads <= 32:
        p.error("Audit threads must be between 1 and 32")
    storage = json.loads((DATA / "storage.json").read_text())
    s3 = boto3.client(
        "s3", region_name=storage["Region"],
        config=Config(max_pool_connections=args.threads,
                      retries={"mode": "adaptive", "total_max_attempts": 8}),
    )
    order, entries = catalogue()
    profiles = {voice: profile(voice) for voice in ["female", "male"]}
    for recipe, _ in profiles.values():
        validate_policy(recipe)
    # Refuse an incomplete batch before scheduling thousands of object reads.
    for voice, (_, version) in profiles.items():
        prefix = f"sentences/{version}/"
        available = {
            item["Key"]
            for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=storage["Bucket"], Prefix=prefix
            )
            for item in page.get("Contents", [])
        }
        missing = sum(
            not all(prefix + audio_id + suffix in available for suffix in [".mp3", ".json"])
            for audio_id in order
        )
        if missing:
            raise SystemExit(f"Incomplete {voice} catalogue: {missing} recordings missing")
    release = {voice: version for voice, (_, version) in profiles.items()}
    release_id = digest({"versions": release, "contentIds": order})
    shards = {f"{i:02x}": {} for i in range(256)}
    human_approvals = []

    def get(key, max_bytes):
        response = s3.get_object(Bucket=storage["Bucket"], Key=key)
        try:
            data = response["Body"].read(max_bytes + 1)
        finally:
            response["Body"].close()
        if len(data) > max_bytes:
            raise ValueError("Object exceeds size limit")
        return data

    def read(audio_id):
        refs = {}
        approvals = []
        for voice, (recipe, version) in profiles.items():
            key = f"sentences/{version}/{audio_id}.mp3"
            manifest = json.loads(get(key[:-4] + ".json", 100_000))
            data = get(key, 2_000_000)
            verify_recording(audio_id, entries[audio_id], recipe, version, manifest, data)
            approval = recording_approval(manifest)
            if approval:
                approvals.append(approval)
            refs[recipe["voiceId"]] = {
                "format": "audio-reference-v1",
                "languageTag": "cmn-Hans",
                "id": "sentence-" + audio_id,
                "voiceId": recipe["voiceId"],
                "version": manifest["sha256"],
                "mimeType": "audio/mpeg",
                "byteLength": len(data),
                "url": f"https://{storage['CdnDomain']}/{key}",
            }
        return audio_id, {**refs["mandarin-female"], "voices": refs}, approvals

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        for count, (audio_id, ref, approvals) in enumerate(pool.map(read, order), 1):
            shards[audio_id[:2]][audio_id] = ref
            human_approvals.extend(approvals)
            if count % 500 == 0:
                print(json.dumps({"verifiedContent": count, "expected": len(order)}), flush=True)
    output = args.output / release_id
    for prefix, values in shards.items():
        atomic_json(output / (prefix + ".json"), values)
    summary = {
        "releaseId": release_id,
        "profiles": release,
        "contents": len(order),
        "recordings": len(order) * 2,
        "complete": True,
        "qualified": True,
        "automatedAccepted": not human_approvals,
        "automatedRecordings": len(order) * 2 - len(human_approvals),
        "humanApprovedRecordings": len(human_approvals),
        "humanApprovals": human_approvals,
        "humanReviewed": False,
    }
    atomic_json(output / "release.json", summary)
    if args.activate:
        import shutil

        target = DATA / "releases" / release_id
        if target.exists():
            for name in ["release.json"] + [f"{i:02x}.json" for i in range(256)]:
                if (target / name).read_bytes() != (output / name).read_bytes():
                    raise ValueError("Release conflict")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(output, target)
        atomic_json(DATA / "active.json", {"releaseId": release_id})
    if args.report:
        atomic_json(args.report, summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
