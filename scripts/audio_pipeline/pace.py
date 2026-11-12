"""Additional regular-speed qualification for story and dialogue delivery.

Measure audible activity, not the MP3 duration: long punctuation pauses must not
hide rushed speech. This is a product pacing band, not a pronunciation check or
a universal linguistic definition of speaking rate. Existing content, ending,
punctuation and integrity checks still apply. No waveform is changed.
"""

import argparse
import hashlib
import json
from pathlib import Path

PACE_POLICY = {
    "version": "mandarin-regular-pace-v1",
    "pace": "regular",
    "frameSeconds": 0.01,
    "activityRelativeRms": 0.08,
    "activityAbsoluteRms": 0.00015,
    "activeSecondsPerSyllable": [0.18, 0.65],
    "singleSyllableActiveSeconds": [0.22, 1.35],
    "maximumSeconds": 90,
    "scope": "Additional regular-speed guard for complete story/narrator/speaker utterances; "
             "quiet pauses do not count as speech. Not a pronunciation or naturalness certificate.",
}


def pcm_digest(samples, sample_rate):
    import numpy as np

    values = np.asarray(samples, dtype="<f4")
    metadata = json.dumps([sample_rate, list(values.shape)], separators=(",", ":")).encode()
    return hashlib.sha256(metadata + b"\0" + values.tobytes()).hexdigest()


def assess_pace(samples, sample_rate, text, pinyin, pace="regular"):
    import numpy as np

    from scripts.audio_pipeline.phonetic import expected_readings

    report = {"policy": PACE_POLICY, "pace": pace, "text": text, "pinyin": pinyin,
              "accepted": False, "failures": []}
    try:
        x = np.asarray(samples, dtype=np.float32)
        if pace != "regular":
            raise ValueError("Only regular pace is calibrated; slow/fast require separate future policies")
        if (not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 96000
                or x.ndim != 1 or not x.size or not np.isfinite(x).all()
                or not 0 < len(x) / sample_rate <= PACE_POLICY["maximumSeconds"]):
            raise ValueError("Pace qualification requires a complete finite mono utterance")
        readings = expected_readings(text, pinyin, character_ends=True) if pinyin else None
        if readings is None or not readings[0]:
            raise ValueError("Authored syllable readings are required for pace qualification")
        count = len(readings[0])
        frame = max(1, round(sample_rate * PACE_POLICY["frameSeconds"]))
        rms = np.sqrt(np.mean(np.pad(x, (0, (-len(x)) % frame)).reshape(-1, frame) ** 2, axis=1))
        threshold = max(PACE_POLICY["activityAbsoluteRms"],
                        float(np.percentile(rms, 90)) * PACE_POLICY["activityRelativeRms"])
        active_seconds = float(np.count_nonzero(rms > threshold)) * frame / sample_rate
        seconds_per_syllable = active_seconds / count
        lower, upper = (PACE_POLICY["singleSyllableActiveSeconds"] if count == 1
                        else PACE_POLICY["activeSecondsPerSyllable"])
        report.update(pcmSha256=pcm_digest(x, sample_rate), sampleRate=sample_rate,
                      durationSeconds=len(x) / sample_rate, syllableCount=count,
                      activeSeconds=active_seconds, activeSecondsPerSyllable=seconds_per_syllable,
                      activeSyllablesPerSecond=count / active_seconds if active_seconds else None,
                      activityThresholdRms=threshold)
        if seconds_per_syllable + 1e-8 < lower:
            report["failures"].append("regular-pace-too-fast")
        if seconds_per_syllable - 1e-8 > upper:
            report["failures"].append("regular-pace-too-slow")
    except (ValueError, TypeError, KeyError) as error:
        report["failures"].append("regular-pace-unverified")
        report["reason"] = str(error)
    report["accepted"] = not report["failures"]
    return report


def verify_pace(samples, sample_rate, text, pinyin, report):
    """Fail closed on changed delivery, source, speed or policy; remeasure pace."""
    try:
        actual_digest = pcm_digest(samples, sample_rate)
    except (ValueError, TypeError, OverflowError):
        return ["regular-pace-unverified"]
    if (not isinstance(report, dict) or report.get("policy") != PACE_POLICY
            or report.get("pace") != "regular" or report.get("text") != text
            or report.get("pinyin") != pinyin
            or report.get("pcmSha256") != actual_digest):
        return ["regular-pace-evidence-missing-or-stale"]
    return assess_pace(samples, sample_rate, text, pinyin)["failures"]


def main():
    import soundfile as sf

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--pinyin", required=True)
    parser.add_argument("--pace", choices=["regular", "slow", "fast"], default="regular")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    samples, rate = sf.read(args.audio, dtype="float32")
    report = assess_pace(samples, rate, args.text, args.pinyin, args.pace)
    digest = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    if args.qualification:
        saved = json.loads(args.qualification.read_text())
        report["failures"] = list(dict.fromkeys(report["failures"] + verify_pace(
            samples, rate, args.text, args.pinyin, saved)))
        if not isinstance(saved, dict) or saved.get("audioSha256") != digest:
            report["failures"].append("regular-pace-encoded-audio-changed")
    report["audioSha256"] = digest
    report["accepted"] = not report["failures"]
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
