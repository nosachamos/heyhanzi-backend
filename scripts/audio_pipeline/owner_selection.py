"""The owner's bounded best-available exception, separate from automatic QA.

No strict policy or voice profile is changed. Failed checks stay failed, and
selection is bound to the exact delivery bytes and ten retained failed attempts.
"""
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

STATUS = "owner-authorized-best-available"
AUTHORIZATION = "course-rollout-owner-2026-10-22"
VERSION = "owner-best-available-v1"
LIMIT = 10

# Retain reviewed v1 receipts across source formatting and the addition of
# separately verified alignment/pace availability branches. Their original
# selection contract and every receipt/byte/waveform check remain unchanged.
COMPATIBLE_IMPLEMENTATIONS = frozenset({
    "218f94900cf28d71c82eb3d5d632f5d6b31c4015d06a6061b8ee62f1e127b692",
    "531ce3c212843eb188c723ac4727085f5203f5ff2fe59dba5e6b148c7e06dce7",
    "e5a5aa57381a44a766392ad269dee40d1f84efc490a1db90f072d7d9fe61680f",
    "2a049bfbfdb753ed48259cfab34ab4683912c570292778fb430fa12dd25f0680",
})


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def implementation_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def owner_syllables(text, pinyin, evidence, duration):
    """Use only observed events, with explicit orthographic rhotic omissions.

    Low-confidence observations remain marked. A missing written non-syllabic
    儿 shares its actually observed base's span; it never creates an event, moves
    an end, proves erhua pronunciation, or qualifies a strict timing receipt.
    """
    from scripts.audio_pipeline.phonetic import expected_readings
    from scripts.audio_pipeline.quality import chinese, transcript_matches
    from scripts.audio_pipeline.timing import TIMING_POLICY, _spellings, is_character_timing

    if not is_character_timing(evidence):
        raise ValueError("Owner timing requires measured primary CTC observations")
    tokens = evidence.get("tokens", [])
    observed = "".join(t.get("text", "") for t in tokens)
    if (observed != evidence.get("transcript") or not tokens
            or any(not re.fullmatch(r"[\u3400-\u9fff]", t.get("text", "")) for t in tokens)
            or not transcript_matches(text, observed, pinyin)):
        raise ValueError("Owner timing does not cover complete ordered spoken content")
    if (not math.isfinite(duration) or duration <= 0
            or not math.isfinite(evidence.get("durationSeconds", float("nan")))
            or abs(evidence["durationSeconds"] - duration) > .0001):
        raise ValueError("Owner timing duration differs from delivered audio")
    previous = 0.0
    for token in tokens:
        start, end, probability = token["start"], token["end"], token["probability"]
        if (not all(math.isfinite(v) for v in (start, end, probability))
                or not previous <= start < end <= duration or not 0 <= probability <= 1):
            raise ValueError("Invalid owner timing observation")
        if evidence["policy"] == TIMING_POLICY:
            if type(token.get("tokenId")) is not int or token["tokenId"] < 0:
                raise ValueError("Invalid owner timing tokenizer identity")
            alternatives = token.get("alternatives", [token])
            identities = [a.get("tokenId") for a in alternatives]
            probabilities = [a.get("probability", float("nan")) for a in alternatives]
            if (not 1 <= len(alternatives) <= 8 or len(set(identities)) != len(identities)
                    or any(type(i) is not int or i < 0 for i in identities)
                    or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)
                    or sum(probabilities) > 1.00001
                    or any(a < b for a, b in zip(probabilities, probabilities[1:]))
                    or any(alternatives[0].get(k) != token.get(k)
                           for k in ("tokenId", "text", "probability"))):
                raise ValueError("Invalid owner timing posterior distribution")
        previous = end
    expected = expected_readings(text, pinyin, character_ends=True)
    if not expected:
        raise ValueError("Cannot map authored reading to real syllables")
    choices, ends = expected
    characters = chinese(text)
    starts = [0, *ends[:-1]]

    @lru_cache(maxsize=None)
    def match(group, offset):
        if group == len(ends):
            return () if offset == len(tokens) else None
        if offset >= len(tokens):
            return None
        begin, finish = starts[group], ends[group]
        rhotic = finish - begin == 2 and characters[finish - 1] == "儿"
        observed_base = tokens[offset]["text"]
        sounds = {v[:-2] + v[-1] if rhotic and v[-2:-1] == "r" else v
                  for v in choices[group]}
        spellings = _spellings(observed_base)
        if (observed_base != characters[begin] and not sounds & spellings
                and not any(v.endswith("5") and v[:-1] == s[:-1]
                            for v in sounds for s in spellings)):
            return None
        lengths = [2, 1] if rhotic else [1]
        for size in lengths:
            if size == 2 and (offset + 1 >= len(tokens) or tokens[offset + 1]["text"] != "儿"):
                continue
            tail = match(group + 1, offset + size)
            if tail is not None:
                return ((offset, size, rhotic and size == 1),) + tail
        return None

    mapping = match(0, 0)
    if mapping is None:
        raise ValueError("No complete ordered mapping of observed syllables")
    rows = []
    for offset, size, omitted in mapping:
        group = tokens[offset:offset + size]
        confidence = min(t["probability"] for t in group)
        rows.append({"start": group[0]["start"], "end": group[-1]["end"],
                     "reliable": confidence >= TIMING_POLICY["minimumPeakProbability"] and not omitted,
                     "probability": confidence, "ownerSelected": True,
                     "orthographicRhoticOmitted": omitted})
    return rows, ends


def timing_summary(text, pinyin, evidence, duration):
    try:
        rows, _ = owner_syllables(text, pinyin, evidence, duration)
        return {"complete": True, "lowConfidenceSyllables": sum(not r["reliable"] for r in rows),
                "omittedWrittenRhoticSuffixes": sum(r["orthographicRhoticOmitted"] for r in rows)}
    except (ValueError, TypeError, KeyError) as error:
        return {"complete": False, "reason": str(error)}


def candidate_score(quality):
    """Lower is better: complete content, endings, timing/phrasing, then MOS."""
    from scripts.audio_pipeline.quality import chinese, transcript_matches

    evidence = quality.get("ownerTimingEvidence") or {}
    transcript = evidence.get("transcript", quality.get("transcript", ""))
    exact = transcript_matches(quality["expectedText"], transcript, quality["intendedPinyin"])
    expected, observed = chinese(quality["expectedText"]), chinese(transcript)
    row = list(range(len(observed) + 1))
    for i, left in enumerate(expected, 1):
        next_row = [i]
        for j, right in enumerate(observed, 1):
            next_row.append(min(row[j] + 1, next_row[-1] + 1, row[j - 1] + (left != right)))
        row = next_row
    endings = quality.get("endings", {})
    ending_failures = endings.get("failures", [])
    noise = "detached-sentence-ending-noise" in ending_failures
    bursts = [b for region in endings.get("regions", []) for b in region.get("bursts", [])]
    timing = quality.get("ownerTiming", {})
    mos = quality.get("dnsmos", {})
    return [0 if exact else 1, 0 if exact else row[-1] / max(1, len(expected)),
            0 if timing.get("complete") else 1,
            2 if noise else 0 if endings.get("accepted") else 1,
            sum(b.get("activeSeconds", 0) for b in bursts),
            timing.get("lowConfidenceSyllables", len(expected)),
            len(quality.get("punctuation", {}).get("failures", [])),
            len(quality.get("failures", [])),
            *[-float(mos.get(key, 0)) for key in ("overall", "signal", "background")]]


def binding(manifest):
    return {key: manifest[key] for key in ("id", "text", "pinyin", "voiceId", "voiceVersion", "sha256", "byteLength")} | {
        "profileSha256": digest(manifest["profile"]), "qualitySha256": digest(manifest["quality"]),
        "attemptsSha256": digest(manifest["attempts"])}


def selection_metadata(manifest, candidates, start, selected):
    return {"version": VERSION, "authorization": AUTHORIZATION, "failureLimit": LIMIT,
            "retentionPassStart": start, "selectedAttempt": selected, "candidates": candidates,
            "binding": binding(manifest), "implementationSha256": implementation_hash()}


def verify_owner_recording(audio_id, entry, recipe, version, manifest, data):
    """Verify the exact explicit exception; never return an automated pass."""
    import io

    import numpy as np
    import soundfile as sf

    from scripts.audio_pipeline.quality import verify_encoded
    from scripts.audio_pipeline.timing import verify_timing_waveform

    selected = manifest.get("ownerSelection", {})
    quality = manifest.get("quality", {})
    if (manifest.get("reviewStatus") != STATUS or manifest.get("humanReviewed") is not False
            or quality.get("accepted") is not False or not quality.get("failures")
            or quality.get("expectedText") != entry["text"] or quality.get("intendedPinyin") != entry["pinyin"]
            or selected.get("version") != VERSION or selected.get("authorization") != AUTHORIZATION
            or selected.get("failureLimit") != LIMIT or selected.get("binding") != binding(manifest)
            or selected.get("implementationSha256") not in COMPATIBLE_IMPLEMENTATIONS | {implementation_hash()}
            or any(manifest.get(k) != v for k, v in {"id": audio_id, "text": entry["text"],
                "pinyin": entry["pinyin"], "voiceId": recipe["voiceId"],
                "voiceVersion": version, "profile": recipe}.items())
            or manifest.get("sha256") != hashlib.sha256(data).hexdigest()
            or manifest.get("byteLength") != len(data) or not 0 < len(data) <= 2_000_000):
        raise ValueError("Invalid exact-byte owner selection")
    start = selected.get("retentionPassStart")
    attempts = manifest["attempts"]
    if (type(start) is not int or start < 0 or len(attempts) - start != LIMIT
            or any(a.get("accepted") is not False or not a.get("failures") for a in attempts[start:])):
        raise ValueError("Owner selection needs ten measured failed attempts")
    candidates = selected.get("candidates", [])
    if (not candidates or len(candidates) > LIMIT
            or len({c["attempt"] for c in candidates}) != len(candidates)
            or any(type(c["attempt"]) is not int or not start <= c["attempt"] < start + LIMIT
                   or len(c["score"]) != 11 or not all(math.isfinite(v) for v in c["score"])
                   for c in candidates)):
        raise ValueError("Invalid retained candidate comparison")
    best = min(candidates, key=lambda c: (c["score"], c["attempt"]))
    if (best["attempt"] != selected.get("selectedAttempt") or best["sha256"] != manifest["sha256"]
            or best["byteLength"] != len(data) or best["score"] != candidate_score(quality)):
        raise ValueError("Owner selection is not the measured best candidate")
    samples, rate = sf.read(io.BytesIO(data), dtype="float32")
    termination = quality.get("termination", {})
    if (rate != 24000 or samples.ndim != 1 or verify_encoded(samples)
            or not np.isfinite(samples).all() or len(samples) / rate < .15
            or termination.get("reason") != "eos"
            or termination.get("generatedSpeechTokens", 0) >= termination.get("maximumSpeechTokens", 0)
            or any(f in quality["failures"] for f in ("no-speech", "incomplete-generation", "invalid-waveform", "abrupt-speech-ending"))
            or abs(len(samples) / rate - manifest["durationSeconds"]) > .025
            or abs(quality["durationSeconds"] - manifest["durationSeconds"]) > .025):
        raise ValueError("Owner selection cannot waive incomplete or unplayable speech")
    evidence = quality.get("ownerTimingEvidence")
    if evidence is not None:
        verify_timing_waveform(samples, rate, evidence)
    if quality.get("ownerTiming") != timing_summary(entry["text"], entry["pinyin"], evidence, len(samples) / rate):
        raise ValueError("Owner timing summary differs from measured delivery")
    if manifest.get("ownerPhoneticAlignment") is not None:
        verify_owner_phonetic_alignment(manifest, data, manifest["ownerPhoneticAlignment"])
    return selected


def verify_owner_phonetic_alignment(manifest, data, proof):
    """Verify one unchanged isolated syllable's strict independent observations.

    The producer additionally reruns the pinned recognizer on the exact MP3.
    Primary-decoder failures and the original ending receipt remain untouched.
    """
    import io

    import soundfile as sf
    from scipy.signal import resample_poly

    from scripts.audio_pipeline.endings import verify_endings
    from scripts.audio_pipeline.phonetic import phonetic_matches, phonetic_review_matches
    from scripts.audio_pipeline.punctuation import observed_syllables
    from scripts.audio_pipeline.quality import chinese
    from scripts.audio_pipeline.timing import sample_digest

    samples, rate = sf.read(io.BytesIO(data), dtype="float32")
    at16 = resample_poly(samples, 16000, rate)
    duration = len(at16) / 16000
    quality = manifest["quality"]
    evidence = proof.get("phoneticEvidence")
    endings = quality.get("endings", {})
    if (proof.get("version") != "owner-isolated-strict-phonetic-alignment-v1"
            or len(chinese(manifest["text"])) != 1
            or proof.get("audioSha256") != manifest["sha256"]
            or hashlib.sha256(data).hexdigest() != manifest["sha256"]
            or proof.get("pcmSha256") != sample_digest(at16)
            or proof.get("text") != manifest["text"] or proof.get("pinyin") != manifest["pinyin"]
            or proof.get("durationSeconds") != duration
            or proof.get("phoneticEvidenceSha256") != digest(evidence)
            or evidence != endings.get("timingEvidence")
            or not phonetic_matches(manifest["text"], manifest["pinyin"], evidence)
            or not phonetic_review_matches(manifest["text"], manifest["pinyin"], quality["transcript"], evidence)
            or verify_endings(samples, rate, manifest["text"], manifest["pinyin"], endings)):
        raise ValueError("Owner phonetic alignment lacks exact strict final-MP3 observations")
    rows, ends = observed_syllables(manifest["text"], manifest["pinyin"], evidence, duration)
    if len(rows) != 1 or ends != [1] or not all(row["reliable"] for row in rows):
        raise ValueError("Owner phonetic alignment does not cover the complete isolated syllable")
    return rows, ends


def verify_fast_pace_recording(audio_id, entry, recipe, version, manifest, data):
    """Accept only the owner's explicit too-fast category, retaining all failures."""
    import io

    import soundfile as sf

    from app.services.audio_catalog_closure import PACE_REVIEW_POLICY, fast_pace_failures_only
    from scripts.audio_pipeline.endings import verify_endings
    from scripts.audio_pipeline.pace import assess_pace, verify_pace
    from scripts.audio_pipeline.phonetic import phonetic_review_matches
    from scripts.audio_pipeline.publish import has_forced_sandhi_prefix
    from scripts.audio_pipeline.punctuation import observed_syllables, verify_punctuation
    from scripts.audio_pipeline.quality import POLICY, chinese, transcript_matches, verify_encoded

    quality = manifest.get("quality", {})
    selection = manifest.get("paceReview", {})
    if (manifest.get("reviewStatus") != PACE_REVIEW_POLICY or manifest.get("humanReviewed") is not False
            or quality.get("accepted") is not False or not fast_pace_failures_only(quality, recipe)
            or selection.get("version") != "owner-fast-pace-review-v1"
            or selection.get("policy") != PACE_REVIEW_POLICY or selection.get("binding") != binding(manifest)
            or not re.fullmatch("[a-f0-9]{64}", selection.get("sourceReceiptSha256", ""))
            or any(manifest.get(key) != value for key, value in
                   {"id": audio_id, "text": entry["text"], "pinyin": entry["pinyin"],
                    "voiceId": recipe["voiceId"], "voiceVersion": version, "profile": recipe}.items())
            or manifest.get("sha256") != hashlib.sha256(data).hexdigest()
            or manifest.get("byteLength") != len(data) or not 0 < len(data) <= 2_000_000
            or has_forced_sandhi_prefix(manifest)):
        raise ValueError("Invalid exact-byte owner fast-pace review")
    samples, rate = sf.read(io.BytesIO(data), dtype="float32")
    count = len(chinese(entry["text"]))
    thresholds = POLICY["shortMos"] if count <= 3 else {
        "signal": POLICY["minimumMosSignal"], "background": POLICY["minimumMosBackground"],
        "overall": POLICY["minimumMosOverall"]}
    ending = quality.get("endings", {})
    rows, _ = observed_syllables(entry["text"], entry["pinyin"], ending.get("timingEvidence"), len(samples) / rate)
    termination = quality.get("termination", {})
    if (rate != 24000 or samples.ndim != 1 or verify_encoded(samples)
            or termination.get("reason") != "eos"
            or not 0 < termination.get("generatedSpeechTokens", 0) < termination.get("maximumSpeechTokens", 0)
            or abs(len(samples) / rate - manifest["durationSeconds"]) > .025
            or abs(quality["durationSeconds"] - manifest["durationSeconds"]) > .025
            or quality.get("expectedText") != entry["text"]
            or not rows or not all(row["reliable"] for row in rows)
            or not (transcript_matches(entry["text"], quality["transcript"], entry["pinyin"])
                    or phonetic_review_matches(entry["text"], entry["pinyin"], quality["transcript"], quality.get("phoneticEvidence")))
            or not 0 <= quality.get("longestPauseSeconds", float("inf")) <= POLICY["maxPauseSeconds"]
            or any(not math.isfinite(quality.get("dnsmos", {}).get(name, float("nan")))
                   or quality["dnsmos"][name] < thresholds[name] for name in ["signal", "background", "overall"])
            or verify_endings(samples, rate, entry["text"], entry["pinyin"], ending)
            or verify_punctuation(samples, rate, entry["text"], entry["pinyin"], quality.get("punctuation"))
            or verify_pace(samples, rate, entry["text"], entry["pinyin"], quality.get("pace")) != ["regular-pace-too-fast"]
            or assess_pace(samples, rate, entry["text"], entry["pinyin"]) != quality.get("pace")):
        raise ValueError("Fast-pace exception cannot waive another delivery or timing failure")
    return selection
