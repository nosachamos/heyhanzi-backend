"""Check pre-generation coverage, optionally verifying every uploaded MP3."""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

from app.services.sentence_audio import DIRECTORY, VOICE_VERSION, catalogue_entry, object_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Download and verify all existing recordings")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--progress", action="store_true", help="Report progress while verifying recordings")
    args = parser.parse_args()
    storage = json.loads((DIRECTORY / "storage.json").read_text())
    s3 = boto3.client("s3", region_name=storage["Region"])
    assets = {
        row["Key"]: row["Size"]
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=storage["Bucket"], Prefix=f"sentences/{VOICE_VERSION}/",
        ) for row in page.get("Contents", [])
    }
    expected = json.loads((DIRECTORY / "generation-order.json").read_text())
    ready = [audio_id for audio_id in expected if object_key(audio_id) in assets]
    missing = [audio_id for audio_id in expected if object_key(audio_id) not in assets]
    manifests_missing = [audio_id for audio_id in ready if object_key(audio_id).removesuffix(".mp3") + ".json" not in assets]
    invalid = []
    verified = 0

    def verify(audio_id):
        key = object_key(audio_id)
        response = s3.get_object(Bucket=storage["Bucket"], Key=key)
        try:
            data = response["Body"].read(2_000_001)
        finally:
            response["Body"].close()
        metadata = s3.get_object(Bucket=storage["Bucket"], Key=key.removesuffix(".mp3") + ".json")
        try:
            manifest = json.loads(metadata["Body"].read(16_385))
        finally:
            metadata["Body"].close()
        digest = hashlib.sha256(data).hexdigest()
        valid = (
            0 < len(data) <= 2_000_000 and len(data) == manifest["byteLength"]
            and digest == manifest["sha256"] == response["Metadata"]["sha256"]
            and manifest["voiceVersion"] == VOICE_VERSION and manifest["id"] == audio_id
            and manifest["text"] == catalogue_entry(audio_id, VOICE_VERSION)["text"]
        )
        return None if valid else audio_id

    if args.verify:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for checked, item in enumerate(pool.map(verify, (i for i in ready if i not in manifests_missing)), 1):
                if item:
                    invalid.append(item)
                else:
                    verified += 1
                if args.progress and checked % 1000 == 0:
                    print(json.dumps({"checked": checked, "recordingsVerified": verified,
                                      "expected": len(expected), "invalid": len(invalid)}), flush=True)
    report = {
        "bucket": storage["Bucket"], "voiceVersion": VOICE_VERSION,
        "expected": len(expected), "ready": len(ready),
        "sentencesReady": sum(catalogue_entry(i, VOICE_VERSION)["kind"] == "sentence" for i in ready),
        "missing": len(missing), "manifestsMissing": len(manifests_missing),
        "invalid": len(invalid), "checksumsVerified": args.verify,
        "recordingsVerified": verified,
        "audioBytes": sum(assets[object_key(i)] for i in ready),
        "missingSample": missing[:10], "invalidSample": invalid[:10],
        "manifestsMissingSample": manifests_missing[:10],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 1 if missing or manifests_missing or invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
