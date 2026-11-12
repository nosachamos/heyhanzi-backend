"""Read current S3 coverage without loading a model or changing the catalogue."""

import json

import boto3

from scripts.audio_pipeline.profiles import DATA, catalogue, profile


def main():
    storage = json.loads((DATA / "storage.json").read_text())
    client = boto3.client("s3", region_name=storage["Region"])
    order, _ = catalogue()
    results = []
    for voice in ["female", "male"]:
        recipe, version = profile(voice)
        prefix = f"sentences/{version}/"
        objects = {
            row["Key"]: row["Size"]
            for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=storage["Bucket"], Prefix=prefix
            )
            for row in page.get("Contents", [])
        }
        ready = [
            i for i in order if prefix + i + ".mp3" in objects and prefix + i + ".json" in objects
        ]
        results.append(
            {
                "voice": voice,
                "voiceVersion": version,
                "expected": len(order),
                "uploaded": len(ready),
                "remaining": len(order) - len(ready),
                "audioBytes": sum(objects[prefix + i + ".mp3"] for i in ready),
            }
        )
    print(
        json.dumps(
            {"voices": results, "complete": all(r["remaining"] == 0 for r in results)}, indent=2
        )
    )


if __name__ == "__main__":
    main()
