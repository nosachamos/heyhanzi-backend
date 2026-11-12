"""Reject isolated noise after sentence endings without cutting speech tails.

CTC token ends are timing witnesses, not acoustic cut points. A normal vowel may
continue beyond a token end. This gate waits for actual quiet, then detects
detached clicks, buzzing or breath bursts before the next protected speech onset.
It never edits audio and does not claim to identify every possible artifact.
"""
import argparse
import json
import math
from pathlib import Path

from scripts.audio_pipeline.punctuation import boundaries, observed_syllables

ENDING_POLICY = {
    "version": "mandarin-clean-endings-v1",
    "frameSeconds": 0.005,
    "leftGuardSeconds": 0.04,
    "nextSpeechGuardSeconds": 0.18,
    "quietSeconds": 0.03,
    "quietAbsoluteRms": 0.0002,
    "quietRelativeRms": 0.0075,
    "noiseAbsoluteRms": 0.001,
    "noiseRelativeRms": 0.03,
    "noiseSeconds": 0.01,
    "clickAbsolutePeak": 0.015,
    "clickRelativePeak": 0.045,
    "maximumSeconds": 30,
    "scope": "Detached audible activity following real quiet at internal sentence "
             "boundaries and after the last spoken syllable. Preserve connected "
             "vowel/nasal decay and the next sentence's consonants; never trim to CTC ends.",
}


def assess_endings(samples, sample_rate, text, pinyin, evidence=None):
    import numpy as np

    report = {"policy": ENDING_POLICY, "regions": [], "failures": [],
              "timingEvidence": evidence, "accepted": False}
    try:
        x = np.asarray(samples, dtype=np.float32)
        if (sample_rate <= 0 or x.ndim != 1 or not x.size or not np.isfinite(x).all()
                or not 0 < len(x) / sample_rate <= ENDING_POLICY["maximumSeconds"]):
            raise ValueError("Ending qualification needs a complete finite mono utterance up to 30 seconds")
        duration = len(x) / sample_rate
        from scripts.audio_pipeline.timing import verify_timing_waveform
        verify_timing_waveform(x, sample_rate, evidence)
        syllables, character_ends = observed_syllables(text, pinyin, evidence, duration)
        if not syllables or not syllables[-1]["reliable"]:
            raise ValueError("Last spoken syllable needs reliable independent timing")
        intervals = []
        for boundary in boundaries(text):
            if boundary["kind"] != "sentence":
                continue
            index = character_ends.index(boundary["afterCharacter"])
            left, right = syllables[index], syllables[index + 1]
            if not left["reliable"] or not right["reliable"]:
                raise ValueError("Sentence ending needs reliable independent timing")
            intervals.append({"afterCharacter": boundary["afterCharacter"],
                              "start": left["end"] + ENDING_POLICY["leftGuardSeconds"],
                              "end": right["start"] - ENDING_POLICY["nextSpeechGuardSeconds"]})
        intervals.append({"afterCharacter": character_ends[-1], "terminal": True,
                          "start": syllables[-1]["end"] + ENDING_POLICY["leftGuardSeconds"],
                          "end": duration})
        frame = max(1, round(sample_rate * ENDING_POLICY["frameSeconds"]))
        frame_seconds = frame / sample_rate
        values = np.pad(x, (0, (-len(x)) % frame)).reshape(-1, frame)
        rms = np.sqrt(np.mean(values ** 2, axis=1))
        peaks = np.max(np.abs(values), axis=1)
        speech_rms = float(np.percentile(rms, 90))
        speech_peak = float(np.percentile(peaks, 95))
        quiet_threshold = max(ENDING_POLICY["quietAbsoluteRms"], speech_rms * ENDING_POLICY["quietRelativeRms"])
        noise_threshold = max(ENDING_POLICY["noiseAbsoluteRms"], speech_rms * ENDING_POLICY["noiseRelativeRms"])
        click_threshold = max(ENDING_POLICY["clickAbsolutePeak"], speech_peak * ENDING_POLICY["clickRelativePeak"])
        quiet_frames = math.ceil(ENDING_POLICY["quietSeconds"] / frame_seconds)
        report["thresholds"] = {"quietRms": quiet_threshold, "noiseRms": noise_threshold,
                                "clickPeak": click_threshold, "speechRms90": speech_rms}
        for interval in intervals:
            start = max(0, math.ceil(interval["start"] / frame_seconds))
            end = (len(rms) if interval.get("terminal") else
                   min(len(x) // frame, math.floor(interval["end"] / frame_seconds)))
            row = {**interval, "bursts": [], "accepted": True}
            quiet_run = 0
            armed = False
            candidate = None
            bursts = []
            for index in range(start, end):
                quiet = rms[index] <= quiet_threshold
                quiet_run = quiet_run + 1 if quiet else 0
                if candidate is not None and quiet_run >= quiet_frames:
                    finish = index - quiet_run + 1
                    bursts.append((candidate, finish))
                    candidate = None
                if quiet_run >= quiet_frames:
                    armed = True
                elif armed and not quiet and candidate is None:
                    candidate = index
            # Measure only the unprotected region. A detached burst continuing
            # to its edge must not escape qualification merely because it never
            # returns to quiet before EOF or the next-speech protection window.
            if candidate is not None:
                bursts.append((candidate, end))
            for begin, finish in bursts:
                active = rms[begin:finish] >= noise_threshold
                noisy_seconds = float(np.count_nonzero(active)) * frame_seconds
                peak = float(np.max(peaks[begin:finish]))
                if noisy_seconds + 1e-8 >= ENDING_POLICY["noiseSeconds"] or peak >= click_threshold:
                    row["bursts"].append({"start": begin * frame_seconds,
                                          "end": min(duration, finish * frame_seconds),
                                          "activeSeconds": noisy_seconds, "peak": peak,
                                          "maximumRms": float(np.max(rms[begin:finish]))})
            row["accepted"] = not row["bursts"]
            report["regions"].append(row)
        if any(not row["accepted"] for row in report["regions"]):
            report["failures"].append("detached-sentence-ending-noise")
    except (ValueError, TypeError, KeyError, IndexError) as error:
        report["failures"].append("ending-noise-unverified")
        report["reason"] = str(error)
    report["accepted"] = not report["failures"]
    return report


def verify_endings(samples, sample_rate, text, pinyin, report):
    if not isinstance(report, dict) or report.get("policy") != ENDING_POLICY:
        return ["ending-evidence-missing-or-stale"]
    return assess_endings(samples, sample_rate, text, pinyin,
                          report.get("timingEvidence"))["failures"]


def main():
    import soundfile as sf

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--pinyin", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", type=Path)
    source.add_argument("--qualification", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    samples, rate = sf.read(args.audio, dtype="float32")
    if args.qualification:
        saved = json.loads(args.qualification.read_text())
        failures = verify_endings(samples, rate, args.text, args.pinyin, saved)
        report = assess_endings(samples, rate, args.text, args.pinyin, saved.get("timingEvidence"))
        report.update(failures=failures, accepted=not failures)
    else:
        report = assess_endings(samples, rate, args.text, args.pinyin, json.loads(args.evidence.read_text()))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"accepted": report["accepted"], "failures": report["failures"]}))
    raise SystemExit(0 if report["accepted"] else 1)


if __name__ == "__main__":
    main()
