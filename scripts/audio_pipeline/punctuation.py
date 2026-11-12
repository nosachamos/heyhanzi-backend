"""Offline punctuation-boundary qualification, not an intonation certificate.

Use unprompted CTC observations to locate each authored boundary, then measure
the delivered waveform there. A pause elsewhere cannot satisfy it. The pinned
primary character observer and strict phonetic fallback remain distinct methods.
"""

import argparse
import json
import math
import re
from pathlib import Path

PUNCTUATION_POLICY = {
    "version": "mandarin-punctuation-v1",
    "minimumIntervalSeconds": {"sentence": 0.24, "clause": 0.12, "list": 0.08,
                               "semicolon": 0.18},
    "minimumQuietSeconds": {"sentence": 0.06, "clause": 0.0, "list": 0.0,
                            "semicolon": 0.03},
    "frameSeconds": 0.01,
    "quietRelativeRms": 0.08,
    "quietAbsoluteRms": 0.00015,
    "minimumBaseTokenProbability": 0.98,
    "minimumToneTokenProbability": 0.5,
    "maximumSeconds": 30,
    "durationToleranceSeconds": 0.025,
    "scope": "Internal punctuation phrasing; terminal completeness uses existing EOS gates. "
             "Does not certify question/exclamation intonation or lexical tones.",
}


def boundaries(text):
    """Collapse adjacent punctuation; never demand pauses for quotes alone/end padding."""
    characters = list(re.finditer(r"[\u3400-\u9fff]", text))
    result = []
    for index, (left, right) in enumerate(zip(characters, characters[1:]), 1):
        separator = text[left.end():right.start()]
        if re.search(r"[。！？.!?…\n\r]", separator):
            kind = "sentence"
        elif re.search(r"[；;：:—]", separator):
            kind = "semicolon"
        elif re.search(r"[，,]", separator):
            kind = "clause"
        elif "、" in separator:
            kind = "list"
        else:
            continue
        result.append({"afterCharacter": index, "punctuation": separator, "kind": kind})
    return result


def observed_syllables(text, pinyin, evidence, duration):
    from scripts.audio_pipeline.timing import character_observations, is_character_timing

    if is_character_timing(evidence):
        return character_observations(text, pinyin, evidence, duration)
    # These are timing witnesses, not a second tone-acceptance path. The existing
    # primary-ASR/strict phonetic-fallback gates still determine pronunciation.
    from scripts.audio_pipeline.phonetic import PHONETIC_POLICY, expected_readings

    if (not isinstance(evidence, dict) or evidence.get("policy") != PHONETIC_POLICY
            or not pinyin):
        raise ValueError("Missing pinned timing evidence or authored pinyin")
    recorded_duration = evidence.get("durationSeconds", 0)
    if (not math.isfinite(recorded_duration)
            or abs(recorded_duration - duration) > PUNCTUATION_POLICY["durationToleranceSeconds"]):
        raise ValueError("Timing evidence belongs to a different duration")
    expected = expected_readings(text, pinyin, character_ends=True)
    if expected is None:
        raise ValueError("Cannot map authored readings to characters")
    choices, character_ends = expected
    syllables, pending = [], []
    previous = 0.0
    for token in evidence.get("tokens", []):
        value, start, end, confidence = (
            token["text"], token["start"], token["end"], token["probability"]
        )
        tone = value in {"1", "2", "3", "4", "5"}
        if (not all(math.isfinite(v) for v in [start, end, confidence])
                or not previous <= start < end <= duration
                or not 0 <= confidence <= 1
                or not re.fullmatch(r"[a-züv]+|[1-5]", value)):
            raise ValueError("Unreliable or invalid timing tokens")
        previous = end
        pending.append(token)
        if tone:
            reading = "".join(t["text"] for t in pending).replace("v", "ü")
            reliable = len(pending) > 1 and all(
                t["probability"] >= PUNCTUATION_POLICY[
                    "minimumToneTokenProbability" if t["text"] in {"1", "2", "3", "4", "5"}
                    else "minimumBaseTokenProbability"
                ] for t in pending
            )
            syllables.append({"reading": reading, "start": pending[0]["start"], "end": end,
                              "reliable": reliable})
            pending = []
    if (pending or len(syllables) != len(choices)
            or any(s["reading"][:-1] not in {v[:-1] for v in options}
                   for s, options in zip(syllables, choices))):
        raise ValueError("Observed syllables do not cover the authored utterance")
    return syllables, character_ends


def assess_punctuation(samples, sample_rate, text, pinyin, evidence=None):
    import numpy as np

    expected = boundaries(text)
    report = {"policy": PUNCTUATION_POLICY, "boundaries": [], "failures": [],
              "timingEvidence": evidence, "accepted": False}
    if not expected:
        report.update(accepted=True, status="not-applicable", timingEvidence=None)
        return report
    report["status"] = "checked"
    x = np.asarray(samples, dtype=np.float32)
    duration = len(x) / sample_rate
    try:
        if (x.ndim != 1 or not np.isfinite(x).all()
                or not 0 < duration <= PUNCTUATION_POLICY["maximumSeconds"]):
            raise ValueError("Punctuation timing supports complete mono clips up to 30 seconds")
        from scripts.audio_pipeline.timing import verify_timing_waveform
        verify_timing_waveform(x, sample_rate, evidence)
        syllables, ends = observed_syllables(text, pinyin, evidence, duration)
        # Only whole 10ms frames lying between the observed syllables count.
        frame = round(sample_rate * PUNCTUATION_POLICY["frameSeconds"])
        rms = np.sqrt(np.mean(np.pad(x, (0, (-len(x)) % frame)).reshape(-1, frame) ** 2, axis=1))
        threshold = max(PUNCTUATION_POLICY["quietAbsoluteRms"],
                        float(np.percentile(rms, 90)) * PUNCTUATION_POLICY["quietRelativeRms"])
        for boundary in expected:
            index = ends.index(boundary["afterCharacter"])
            left, right = syllables[index], syllables[index + 1]
            interval = right["start"] - left["end"]
            start = math.ceil(left["end"] * sample_rate / frame)
            end = math.floor(right["start"] * sample_rate / frame)
            longest = run = 0
            for quiet in rms[start:end] < threshold:
                run = run + 1 if quiet else 0
                longest = max(longest, run)
            quiet_seconds = longest * frame / sample_rate
            kind = boundary["kind"]
            reliable = left["reliable"] and right["reliable"]
            passed = (reliable
                      and interval + 1e-8 >= PUNCTUATION_POLICY["minimumIntervalSeconds"][kind]
                      and quiet_seconds + 1e-8 >= PUNCTUATION_POLICY["minimumQuietSeconds"][kind])
            report["boundaries"].append({**boundary, "leftEnd": left["end"],
                                         "rightStart": right["start"],
                                         "intervalSeconds": round(interval, 6),
                                         "quietSeconds": round(quiet_seconds, 6),
                                         "timingReliable": reliable,
                                         "accepted": passed})
        if any(not row["accepted"] and row["timingReliable"] for row in report["boundaries"]):
            report["failures"].append("punctuation-boundary-too-short")
        if any(not row["timingReliable"] for row in report["boundaries"]):
            report["failures"].append("punctuation-timing-unverified")
    except (ValueError, TypeError, KeyError, IndexError) as error:
        report["failures"].append("punctuation-timing-unverified")
        report["reason"] = str(error)
    report["accepted"] = not report["failures"]
    return report


def verify_punctuation(samples, sample_rate, text, pinyin, report):
    """Release/resume must recompute the gate, not trust a persisted accepted flag."""
    if not isinstance(report, dict) or report.get("policy") != PUNCTUATION_POLICY:
        return ["punctuation-evidence-missing-or-stale"]
    measured = assess_punctuation(samples, sample_rate, text, pinyin, report.get("timingEvidence"))
    return measured["failures"]


def main():
    """Recheck delivered MP3s using stored, independently recognized timing evidence."""
    import soundfile as sf

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--pinyin", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--evidence", type=Path)
    source.add_argument("--qualification", type=Path,
                        help="Revalidate a saved punctuation report, as resume/release do")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    samples, rate = sf.read(args.audio, dtype="float32")
    saved = json.loads(args.qualification.read_text()) if args.qualification else None
    evidence = (saved.get("timingEvidence") if isinstance(saved, dict)
                else json.loads(args.evidence.read_text()) if args.evidence else None)
    report = assess_punctuation(samples, rate, args.text, args.pinyin, evidence)
    if args.qualification:
        report["failures"] = verify_punctuation(samples, rate, args.text, args.pinyin, saved)
        report["accepted"] = not report["failures"]
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
