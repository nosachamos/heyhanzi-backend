"""Conservative finishing and automated checks, outside the API environment."""

import hashlib
import math
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from scripts.audio_pipeline.endings import ENDING_POLICY, assess_endings
from scripts.audio_pipeline.phonetic import (
    PHONETIC_POLICY,
    PhoneticRecognizer,
    phonetic_review_matches,
)
from scripts.audio_pipeline.punctuation import (
    PUNCTUATION_POLICY,
    assess_punctuation,
)
from scripts.audio_pipeline.timing import TIMING_POLICY, CharacterTimingRecognizer

POLICY = {
    "version": "mandarin-clean-v5",
    "paddingMs": 80,
    "fadeMs": 6,
    "targetSpeechRmsDb": -22,
    "maxGainDb": 15,
    "peakCeiling": 0.82,
    "maxPauseSeconds": 0.75,
    "secondsPerCharacter": [0.13, 0.65],
    "singleCharacterSeconds": [0.22, 1.35],
    "maxAttempts": 24,
    "maxSeconds": 90,
    "vad": "silero-vad==6.2.0",
    "asr": "FunAudioLLM/SenseVoiceSmall",
    "asrRevision": "3847d57b6bdf2dd8875cb1508d2af43d80a16bf7",
    "dnsmosRevision": "591184a9fcb2cbdec02520fed81a32bbbf9d73ff",
    "dnsmosSha256": "269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd",
    "shortMos": {"maxCharacters": 3, "signal": 2.5, "background": 3.3, "overall": 2.2},
    "minimumMosSignal": 3.0,
    "minimumMosBackground": 3.5,
    "minimumMosOverall": 2.8,
    "acceptance": "automated; not a native-speaker certification",
    "phoneticFallback": PHONETIC_POLICY,
    "punctuation": PUNCTUATION_POLICY,
    "endings": ENDING_POLICY,
    "characterTiming": TIMING_POLICY,
}


def chinese(text):
    return "".join(re.findall(r"[\u3400-\u9fff]", text))


def transcript_matches(text, transcript, intended_pinyin=None):
    """Compare content without treating orthographic homophones as missing speech.

    ASR spells 他/她 interchangeably and commonly omits the written 儿 suffix.
    Compare only changed characters; an unchanged 学生 must not make a 他/她
    comparison fail because the dictionary and curriculum differ on neutral tone.
    This is a content check, not acoustic verification of tones or erhua.
    """
    from pypinyin import Style, lazy_pinyin, pinyin
    from pypinyin.contrib.tone_convert import to_normal

    expected, actual = chinese(text), chinese(transcript)
    if expected == actual:
        return True

    def key(value):
        return "".join(c for c in value.lower().split("/")[0] if c.isalpha()).replace("v", "ü")

    @lru_cache(maxsize=256)
    def readings(character):
        return set(pinyin(character, style=Style.TONE, heteronym=True)[0])

    contextual = lazy_pinyin(expected, style=Style.TONE)
    sounds = key(intended_pinyin) if intended_pinyin else "".join(contextual)

    @lru_cache(maxsize=None)
    def align(index, offset):
        if index == len(expected):
            return () if offset == len(sounds) else None
        options = readings(expected[index]) | {contextual[index]}
        # Neutral-tone syllables in curricular pinyin retain their exact reading.
        options |= {to_normal(value) for value in options}
        if expected[index] == "儿":
            options.add("r")
        for value in sorted(options, key=lambda s: (-len(s), s)):
            if sounds.startswith(value, offset):
                tail = align(index + 1, offset + len(value))
                if tail is not None:
                    return (value,) + tail
        return None

    intended = align(0, 0)
    if intended is None:
        return False
    actual_context = lazy_pinyin(actual, style=Style.TONE)

    @lru_cache(maxsize=None)
    def matches(i, j):
        if i == len(expected):
            return j == len(actual)
        if j < len(actual):
            observed = readings(actual[j]) | {actual_context[j]}
            # ASR orthography can use the Taiwan Mandarin reading qí of 期
            # (MOE Revised Dictionary, 期間, ID=98499). This supplements only
            # observed spellings; it never changes authored Mainland readings.
            # https://dict.revised.moe.edu.tw/dictView.jsp?ID=98499&la=1&powerMode=0
            if actual[j] == "期":
                observed.add("qí")
            # Neutral syllables have no lexical tone to recover from an ASR
            # spelling (isolated le may be written 乐). Preserve the syllable;
            # do not strip tones from a tone-bearing curricular reading.
            equivalent = intended[i] in observed or (
                intended[i] == to_normal(intended[i])
                and intended[i] in {to_normal(value) for value in observed}
            )
            if (expected[i] == actual[j] or equivalent) and matches(i + 1, j + 1):
                return True
        # Only a curricular non-syllabic r may be unwritten by ASR. Never skip
        # 儿 pronounced ér (child), or another missing character/syllable.
        return expected[i] == "儿" and intended[i] == "r" and matches(i + 1, j)

    return matches(0, 0)


class Quality:
    def __init__(self, asr_dir, device="cuda:0", phonetic_dir=None):
        self.phonetic_dir = phonetic_dir
        self.phonetic = None
        from funasr import AutoModel
        from silero_vad import get_speech_timestamps, load_silero_vad

        self.vad = load_silero_vad(onnx=True)
        self.timestamps = get_speech_timestamps
        import onnxruntime as ort

        mos_path = Path(asr_dir) / "sig_bak_ovr.onnx"
        if hashlib.sha256(mos_path.read_bytes()).hexdigest() != POLICY["dnsmosSha256"]:
            raise ValueError("DNSMOS checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.mos = ort.InferenceSession(
            str(mos_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        weights = Path(asr_dir) / "model.pt"
        revision = (
            (Path(asr_dir) / ".cache/huggingface/download/model.pt.metadata")
            .read_text()
            .splitlines()[0]
        )
        if (
            not weights.is_file()
            or weights.stat().st_size < 100_000_000
            or revision != POLICY["asrRevision"]
        ):
            raise ValueError("Complete pinned ASR weights are required")
        self.asr = AutoModel(
            model=str(asr_dir),
            init_param=str(weights),
            device=device,
            disable_update=True,
            disable_pbar=True,
            disable_log=True,
        )

    def timing_evidence(self, samples, text, pinyin, primary=None):
        from scripts.audio_pipeline.punctuation import observed_syllables

        if len(samples) / 16000 > TIMING_POLICY["maximumSeconds"]:
            return None
        evidence = primary or CharacterTimingRecognizer(self.asr).recognize(samples)
        try:
            rows, _ = observed_syllables(text, pinyin, evidence, len(samples) / 16000)
            if not all(row["reliable"] for row in rows):
                raise ValueError("Uncertain character timing")
            return evidence
        except ValueError:
            if not self.phonetic_dir:
                raise
            if self.phonetic is None:
                self.phonetic = PhoneticRecognizer(self.phonetic_dir)
            return self.phonetic.recognize(samples)

    def speech_quality(self, samples):
        # DNSMOS public inference procedure (Microsoft DNS-Challenge, CC BY 4.0).
        # Short clips are repeated to fill the model's 9.01-second input window.
        # Scores are distortion/noise indicators, not a pronunciation certificate.
        size = 144160
        x = np.asarray(samples, dtype=np.float32)
        while len(x) < size:
            x = np.concatenate([x, x])
        scores = []
        for start in range(0, len(x) - size + 1, 16000):
            raw = self.mos.run(None, {"input_1": x[start : start + size][None, :]})[0][0]
            scores.append(
                [
                    float(np.polyval(c, v))
                    for c, v in zip(
                        [
                            (-0.08397278, 1.22083953, 0.0052439),
                            (-0.13166888, 1.60915514, -0.39604546),
                            (-0.06766283, 1.11546468, 0.04602535),
                        ],
                        raw,
                    )
                ]
            )
        signal, background, overall = np.mean(scores, axis=0)
        return {"signal": float(signal), "background": float(background), "overall": float(overall)}

    def assess(self, waveform, sample_rate, text, termination, pinyin=None):
        """Return finished samples and explicit failure reasons. Never fix truncation."""
        x = np.asarray(waveform, dtype=np.float32)
        failures = []
        metrics = {"termination": termination}
        if x.ndim != 1 or not x.size or not np.isfinite(x).all():
            return x, {**metrics, "accepted": False, "failures": ["invalid-waveform"]}
        if termination.get("reason") != "eos":
            failures.append("incomplete-generation")
        if (
            abs(float(x[-1])) > 0.005
            and float(np.sqrt(np.mean(x[-max(1, sample_rate // 10) :] ** 2))) > 0.01
        ):
            failures.append("abrupt-speech-ending")
        if np.max(np.abs(x)) >= 1:
            failures.append("raw-clipping")
        if not 0.15 <= len(x) / sample_rate <= POLICY["maxSeconds"]:
            failures.append("invalid-duration")
        at16 = (
            resample_poly(x, 2, 3) if sample_rate == 24000 else resample_poly(x, 16000, sample_rate)
        )
        spans = self.timestamps(
            at16,
            self.vad,
            sampling_rate=16000,
            threshold=0.35,
            min_speech_duration_ms=80,
            min_silence_duration_ms=150,
            speech_pad_ms=0,
        )
        if not spans:
            return x, {**metrics, "accepted": False, "failures": failures + ["no-speech"]}
        # Use VAD to locate speech, but extend to nearby quiet boundaries to retain
        # unvoiced consonants. Internal pauses are measured, never squeezed out.
        frame = max(1, sample_rate // 100)
        rms = np.sqrt(np.mean(np.pad(x, (0, (-len(x)) % frame)).reshape(-1, frame) ** 2, axis=1))
        threshold = max(0.00015, float(np.percentile(rms, 90)) * 0.025)
        start = max(0, int(spans[0]["start"] / 16000 * sample_rate) // frame - 8)
        end = min(len(rms), int(spans[-1]["end"] / 16000 * sample_rate) // frame + 9)
        while start > 0 and rms[start - 1] > threshold:
            start -= 1
        while end < len(rms) and rms[end] > threshold:
            end += 1
        # Significant activity outside the selected speech region may be an extra
        # syllable or noise. Reject rather than silently cut it off.
        outside = np.concatenate([rms[: max(0, start - 5)], rms[min(len(rms), end + 5) :]])
        if outside.size and np.max(outside) > max(0.004, float(np.max(rms)) * 0.15):
            failures.append("unexplained-boundary-activity")
        begin, finish = start * frame, min(len(x), end * frame)
        trimmed = x[begin:finish].copy()
        activity = rms[start:end] > max(threshold, float(np.percentile(rms, 90)) * 0.08)
        active_indices = np.flatnonzero(activity)
        if active_indices.size:
            gaps = np.diff(active_indices) - 1
            pause = float(max(gaps, default=0)) * 0.01
            speech_seconds = float(np.sum(activity)) * 0.01
        else:
            pause, speech_seconds = 0.0, 0.0
        n = len(chinese(text))
        span_seconds = (spans[-1]["end"] - spans[0]["start"]) / 16000
        if pause > POLICY["maxPauseSeconds"]:
            failures.append("excessive-pause")
        lower, upper = POLICY["singleCharacterSeconds"] if n <= 1 else POLICY["secondsPerCharacter"]
        pace = speech_seconds / max(1, n)
        if not lower <= pace <= upper:
            failures.append("pace-outside-policy")
        active_rms = (
            float(np.sqrt(np.mean(trimmed[abs(trimmed) > threshold] ** 2)))
            if np.any(abs(trimmed) > threshold)
            else 0.0
        )
        gain = min(
            10 ** (POLICY["maxGainDb"] / 20),
            10 ** (POLICY["targetSpeechRmsDb"] / 20) / max(active_rms, 1e-8),
            POLICY["peakCeiling"] / max(float(np.max(abs(trimmed))), 1e-8),
        )
        trimmed *= gain
        fade = min(round(sample_rate * POLICY["fadeMs"] / 1000), len(trimmed) // 2)
        trimmed[:fade] *= np.linspace(0, 1, fade)
        trimmed[-fade:] *= np.linspace(1, 0, fade)
        padding = round(sample_rate * POLICY["paddingMs"] / 1000)
        finished = np.pad(trimmed, (padding, padding))
        at16 = resample_poly(finished, 16000, sample_rate)
        mos = self.speech_quality(at16)
        if (
            not all(math.isfinite(value) for value in mos.values())
            or mos["signal"]
            < (POLICY["shortMos"]["signal"] if n <= 3 else POLICY["minimumMosSignal"])
            or mos["background"]
            < (POLICY["shortMos"]["background"] if n <= 3 else POLICY["minimumMosBackground"])
            or mos["overall"]
            < (POLICY["shortMos"]["overall"] if n <= 3 else POLICY["minimumMosOverall"])
        ):
            failures.append("speech-quality-below-policy")
        if len(at16) / 16000 <= TIMING_POLICY["maximumSeconds"]:
            timing_evidence = CharacterTimingRecognizer(self.asr).recognize(at16)
            transcript = timing_evidence["transcript"]
        else:
            timing_evidence = None
            result = self.asr.generate(input=at16, cache={}, language="zh", use_itn=False,
                                       batch_size_s=60, disable_pbar=True)
            transcript = re.sub(r"<\|.*?\|>", "", result[0].get("text", ""))
        phonetic_evidence = None
        if not transcript_matches(text, transcript, pinyin):
            # Orthographic decoders can substitute a more frequent written word.
            # A second unprompted recognizer must independently recover EVERY
            # intended syllable/tone confidently; no edit-distance allowance.
            if not failures and self.phonetic_dir and pinyin:
                if self.phonetic is None:
                    self.phonetic = PhoneticRecognizer(self.phonetic_dir)
                phonetic_evidence = self.phonetic.recognize(at16)
            if not phonetic_review_matches(text, pinyin, transcript, phonetic_evidence):
                failures.append("transcript-mismatch")
        # Character timing is the primary decoder's own observation, not an
        # independent pronunciation claim. Preserve the strict phonetic fallback
        # for utterances the orthographic recognizer cannot represent faithfully.
        timing_evidence = self.timing_evidence(at16, text, pinyin, timing_evidence)
        punctuation = assess_punctuation(finished, sample_rate, text, pinyin, timing_evidence)
        failures.extend(punctuation["failures"])
        endings = assess_endings(finished, sample_rate, text, pinyin, timing_evidence)
        failures.extend(endings["failures"])
        metrics.update(
            {
                "rawSeconds": len(x) / sample_rate,
                "durationSeconds": len(finished) / sample_rate,
                "speechSeconds": speech_seconds,
                "speechSpanSeconds": span_seconds,
                "secondsPerCharacter": pace,
                "longestPauseSeconds": pause,
                "trimStartSeconds": begin / sample_rate,
                "trimEndSeconds": (len(x) - finish) / sample_rate,
                "gainDb": float(20 * np.log10(max(gain, 1e-8))),
                "transcript": transcript,
                "phoneticEvidence": phonetic_evidence,
                "punctuation": punctuation,
                "endings": endings,
                "expectedText": text,
                "dnsmos": mos,
                "failures": failures,
                "accepted": not failures,
            }
        )
        return finished, metrics


def verify_encoded(samples):
    samples = np.asarray(samples)
    failures = []
    if not samples.size or not np.isfinite(samples).all():
        return ["invalid-encoded-waveform"]
    if np.max(abs(samples)) >= 1:
        failures.append("encoded-clipping")
    if max(abs(float(samples[0])), abs(float(samples[-1]))) > 0.001:
        failures.append("encoded-boundary-discontinuity")
    return failures
