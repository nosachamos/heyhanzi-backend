"""Validate optional sentence delivery without changing authored story spans."""
import math
import re


def _need(condition):
    if not condition:
        raise ValueError("Invalid story sentence delivery")


def _bounded(paragraph, metadata):
    ids = [token["id"] for token in paragraph["tokens"]]
    _need(metadata.get("firstTokenId") in ids and metadata.get("lastTokenId") in ids)
    first, last = ids.index(metadata["firstTokenId"]), ids.index(metadata["lastTokenId"])
    _need(first <= last)
    return paragraph["tokens"][first:last + 1]


def _reference(reference):
    _need(isinstance(reference, dict) and set(reference) == {"audio", "alignment"})
    for field in ["audio", "alignment"]:
        value = reference[field]
        _need(isinstance(value, dict) and isinstance(value.get("url"), str) and bool(value["url"])
              and isinstance(value.get("sha256"), str) and re.fullmatch("[a-f0-9]{64}", value["sha256"])
              and type(value.get("byteLength")) is int and value["byteLength"] > 0)
    duration = reference["audio"].get("durationSeconds")
    _need(type(duration) in {int, float} and math.isfinite(duration) and duration > 0)


def _delivery(tokens, delivery):
    if not isinstance(delivery, dict) or "clips" not in delivery:
        return  # Keep the existing whole-recording contract compatible.
    _need(set(delivery) == {"clips"} and isinstance(delivery["clips"], list) and len(delivery["clips"]) >= 2)
    ids = [token["id"] for token in tokens]
    _need(len(ids) == len(set(ids)))
    following = 0
    seen = set()
    for index, clip in enumerate(delivery["clips"]):
        _need(isinstance(clip, dict) and set(clip) <= {"id", "firstTokenId", "lastTokenId", "reference", "pauseAfterSeconds"}
              and {"id", "firstTokenId", "lastTokenId", "reference"} <= set(clip))
        _need(isinstance(clip["id"], str) and bool(clip["id"]) and clip["id"] not in seen
              and clip["firstTokenId"] in ids and clip["lastTokenId"] in ids)
        first, last = ids.index(clip["firstTokenId"]), ids.index(clip["lastTokenId"])
        _need(first == following and last >= first)
        bounded = tokens[first:last + 1]
        text = "".join(token["text"] for token in bounded)
        final_cue = (index == len(delivery["clips"]) - 1 and text.endswith(("：", ":"))
                     and "".join(token["text"] for token in tokens).endswith(("：", ":")))
        _need(any(token.get("pinyin") for token in bounded)
              and (re.search(r'[。！？!?][”’」』"]?$', text) or final_cue))
        _reference(clip["reference"])
        pause = clip.get("pauseAfterSeconds", 0)
        _need(type(pause) in {int, float} and pause in ({0} if index == len(delivery["clips"]) - 1 else {0, .25}))
        seen.add(clip["id"])
        following = last + 1
    _need(following == len(tokens))


def validate(activity):
    """Validate sentence clips wherever the existing manifest allows a delivery."""
    manifest = activity.get("recordings", {})
    for paragraph in activity.get("sample", {}).get("paragraphs", []):
        pid = paragraph["id"]
        for delivery in manifest.get("paragraphs", {}).get(pid, {}).values():
            _delivery(paragraph["tokens"], delivery)
        for recording in manifest.get("narration", {}).get(pid, {}).values():
            if "clips" in recording:
                _delivery(paragraph["tokens"], recording)
            elif "segments" in recording:
                for segment in recording["segments"]:
                    _delivery(_bounded(paragraph, segment), segment.get("reference"))
            else:
                _delivery(paragraph["tokens"], recording)
        for segment in manifest.get("dialogue", {}).get(pid, {}).get("segments", []):
            _delivery(_bounded(paragraph, segment), segment.get("reference"))
