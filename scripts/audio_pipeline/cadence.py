"""Bao's approved natural delivery: measured speech pace and meaningful sentence pauses.

Supplementary to clean-v5, not a replacement pronunciation or naturalness test.
No samples are changed. Legacy profiles do not opt into this policy implicitly.
"""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.audio_pipeline.pace import PACE_POLICY, assess_pace, pcm_digest
from scripts.audio_pipeline.punctuation import PUNCTUATION_POLICY, assess_punctuation

CADENCE_POLICY = {
    "version": "bao-natural-cadence-v1",
    "referenceAudioSha256": "45c5b9b3496993e7617f8e8f773ef57374559f52ad8f9ae6d3c7bea9dc64b7a6",
    "minimumSentenceIntervalSeconds": 0.48,
    "minimumSentenceQuietSeconds": 0.24,
    "pacePolicy": PACE_POLICY,
    "punctuationPolicy": PUNCTUATION_POLICY,
    "scope": "Regular connected speech and measured internal sentence pauses, calibrated against "
             "the owner-selected Bao candidate A. Preserve shorter clause/list boundaries. "
             "No artificial terminal pause, pitch shifting, silence insertion or time stretching. "
             "Screening only; not a guarantee of subjective naturalness or lexical tones.",
}


def implementation_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def assess_cadence(samples, sample_rate, text, pinyin, evidence=None):
    """Measure final delivery; a pause elsewhere cannot satisfy a sentence boundary."""
    pace = assess_pace(samples, sample_rate, text, pinyin)
    punctuation = assess_punctuation(samples, sample_rate, text, pinyin, evidence)
    report = {"policy": CADENCE_POLICY, "text": text, "pinyin": pinyin,
              "sampleRate": sample_rate, "accepted": False,
              "failures": list(dict.fromkeys(pace["failures"] + punctuation["failures"])),
              "pace": {k: v for k, v in pace.items() if k not in {"policy", "text", "pinyin"}},
              "boundaries": punctuation["boundaries"]}
    try:
        report["pcmSha256"] = pcm_digest(samples, sample_rate)
        for boundary in report["boundaries"]:
            if boundary["kind"] != "sentence":
                continue
            if (boundary["intervalSeconds"] + 1e-8 < CADENCE_POLICY["minimumSentenceIntervalSeconds"]
                    or boundary["quietSeconds"] + 1e-8 < CADENCE_POLICY["minimumSentenceQuietSeconds"]):
                report["failures"].append("bao-sentence-pause-too-short")
                boundary["accepted"] = False
    except (ValueError, TypeError, KeyError, OverflowError):
        report["failures"].append("bao-cadence-unverified")
    report["failures"] = list(dict.fromkeys(report["failures"]))
    report["accepted"] = not report["failures"]
    return report


def verify_cadence(samples, sample_rate, text, pinyin, report, evidence=None):
    """Fail closed on missing/stale delivery, source or policy, then remeasure."""
    try:
        if (not isinstance(report, dict) or report.get("policy") != CADENCE_POLICY
                or report.get("text") != text or report.get("pinyin") != pinyin
                or report.get("sampleRate") != sample_rate
                or report.get("pcmSha256") != pcm_digest(samples, sample_rate)):
            return ["bao-cadence-evidence-missing-or-stale"]
    except (ValueError, TypeError, OverflowError):
        return ["bao-cadence-unverified"]
    return assess_cadence(samples, sample_rate, text, pinyin, evidence)["failures"]


def validate_cadence_profile(recipe):
    """Only explicitly versioned profiles opt in; unknown policy/code fails closed."""
    if "cadencePolicy" not in recipe:
        return
    if (recipe["cadencePolicy"] != CADENCE_POLICY
            or recipe.get("cadenceImplementationSha256") != implementation_hash()):
        raise ValueError("Cadence recipe differs from its calibrated policy or implementation")


def verify_recording_cadence(record, data):
    """Supplement the normal/owner recording verifier on final encoded bytes."""
    import io

    import soundfile as sf

    recipe = record["profile"]
    validate_cadence_profile(recipe)
    if "cadencePolicy" not in recipe:
        return
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("Cadence recording bytes changed")
    samples, rate = sf.read(io.BytesIO(data), dtype="float32")
    quality = record["quality"]
    evidence = (quality.get("punctuation", {}).get("timingEvidence")
                or quality.get("endings", {}).get("timingEvidence"))
    failures = verify_cadence(samples, rate, record["text"], record["pinyin"],
                              quality.get("cadence"), evidence)
    if failures:
        raise ValueError("Recording fails Bao cadence: " + ", ".join(failures))


def main():
    import soundfile as sf

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--pinyin", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    samples, rate = sf.read(args.audio, dtype="float32")
    evidence = json.loads(args.evidence.read_text()) if args.evidence else None
    result = assess_cadence(samples, rate, args.text, args.pinyin, evidence)
    digest = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    if args.qualification:
        saved = json.loads(args.qualification.read_text())
        result["failures"] += verify_cadence(samples, rate, args.text, args.pinyin, saved, evidence)
        if not isinstance(saved, dict) or saved.get("audioSha256") != digest:
            result["failures"].append("bao-cadence-encoded-audio-changed")
    result["audioSha256"] = digest
    result["failures"] = list(dict.fromkeys(result["failures"]))
    result["accepted"] = not result["failures"]
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
