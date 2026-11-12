"""Publish verified, compact CDN references as curriculum metadata.

Run after one-off generation; API requests never read S3 or the full catalogue.
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

from app.services.sentence_audio import DIRECTORY, PROFILE, VOICE_VERSION, object_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DIRECTORY / "published" / VOICE_VERSION,
                        help="Stage a verified metadata snapshot in a separate directory")
    args = parser.parse_args()
    storage = json.loads((DIRECTORY / "storage.json").read_text())
    s3 = boto3.client("s3", region_name=storage["Region"])
    files = {row["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=storage["Bucket"], Prefix=f"sentences/{VOICE_VERSION}/",
    ) for row in page.get("Contents", [])}
    order = json.loads((DIRECTORY / "generation-order.json").read_text())
    ready = [i for i in order if object_key(i) in files and object_key(i).removesuffix(".mp3") + ".json" in files]
    if args.require_complete and len(ready) != len(order):
        raise SystemExit(f"Generation is incomplete: {len(ready)}/{len(order)} recordings")

    def read(audio_id):
        key = object_key(audio_id)
        response = s3.get_object(Bucket=storage["Bucket"], Key=key.removesuffix(".mp3") + ".json")
        try:
            manifest = json.loads(response["Body"].read(16_385))
        finally:
            response["Body"].close()
        if (manifest["id"] != audio_id or manifest["voiceVersion"] != VOICE_VERSION
                or manifest["profile"] != PROFILE or not 0 < manifest["byteLength"] <= 2_000_000
                or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])):
            raise ValueError("Invalid uploaded pronunciation metadata")
        return audio_id, {
            "format": "audio-reference-v1", "languageTag": "cmn-Hans", "id": f"sentence-{audio_id}",
            "version": manifest["sha256"], "mimeType": "audio/mpeg", "byteLength": manifest["byteLength"],
            "url": f"https://{storage['CdnDomain']}/{key}",
        }

    shards = {f"{prefix:02x}": {} for prefix in range(256)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for audio_id, reference in pool.map(read, ready):
            shards[audio_id[:2]][audio_id] = reference
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    for prefix, entries in shards.items():
        target = output / f"{prefix}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, separators=(",", ":")) + "\n")
        temporary.replace(target)
    print(json.dumps({"published": len(ready), "expected": len(order), "cdn": storage["CdnDomain"]}))


if __name__ == "__main__":
    main()
