"""Explicit, scoped retirement of obsolete recording versions (dry-run by default)."""

import argparse
import json
import re

import boto3

from app.services.audio_qualification import release_is_qualified
from scripts.audio_pipeline.profiles import DATA


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--retired-version", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument(
        "--served-release",
        required=True,
        help="Release verified as active in every serving environment",
    )
    args = p.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.retired_version):
        p.error("Invalid namespace")
    active = json.loads((DATA / "active.json").read_text())["releaseId"]
    if args.served_release != active:
        raise SystemExit("Serving release does not match the active snapshot")
    release = json.loads((DATA / "releases" / active / "release.json").read_text())
    if not release_is_qualified(release):
        raise SystemExit("Replacement is not verified complete")
    if args.retired_version in release["profiles"].values():
        raise SystemExit("Cannot delete an active voice")
    storage = json.loads((DATA / "storage.json").read_text())
    s3 = boto3.client("s3", region_name=storage["Region"])
    prefix = "sentences/" + args.retired_version + "/"
    objects = []
    total = 0
    for page in s3.get_paginator("list_object_versions").paginate(
        Bucket=storage["Bucket"], Prefix=prefix
    ):
        for group in ["Versions", "DeleteMarkers"]:
            for item in page.get(group, []):
                if not item["Key"].startswith(prefix):
                    raise ValueError("Out-of-scope object")
                objects.append({"Key": item["Key"], "VersionId": item["VersionId"]})
                total += item.get("Size", 0)
    print(
        json.dumps(
            {
                "namespace": prefix,
                "versionsAndMarkers": len(objects),
                "bytes": total,
                "apply": args.apply,
            }
        ),
        flush=True,
    )
    if args.apply:
        for offset in range(0, len(objects), 1000):
            result = s3.delete_objects(
                Bucket=storage["Bucket"],
                Delete={"Objects": objects[offset : offset + 1000], "Quiet": True},
            )
            if result.get("Errors"):
                raise RuntimeError("S3 did not delete every requested object")


if __name__ == "__main__":
    main()
