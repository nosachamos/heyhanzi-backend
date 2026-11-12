"""Unprompted primary-ASR CTC observations for offline word highlighting.

These are character timing witnesses, not an independent pronunciation review.
The primary content gate and strict phonetic fallback retain their separate roles.
Never force-align authored text or erase unmatched recognized speech.
"""

import hashlib
import math
import re
from functools import lru_cache

import numpy as np
from scipy.signal import resample_poly

TIMING_POLICY_V1 = {
    "version": "sensevoice-greedy-character-timing-v1",
    "model": "FunAudioLLM/SenseVoiceSmall",
    "revision": "3847d57b6bdf2dd8875cb1508d2af43d80a16bf7",
    "frameSeconds": 0.06,
    "frameOffsetSeconds": -0.03,
    "metadataFrames": 4,
    "minimumPeakProbability": 0.90,
    "confidence": "Top-8 posterior mass for authored homophonic spellings at the greedy event peak",
    "maximumSeconds": 30,
    "scope": "Unprompted primary-ASR character observations; not a second tone check",
}

# Keep the exact original policy readable for already-qualified delivery bytes.
# These quiet settings are pinned to the existing ending gate, not a new pause
# tolerance. A future change needs a new timing policy and fresh qualification.
TIMING_POLICY_V2 = {
    **TIMING_POLICY_V1,
    "version": "sensevoice-greedy-character-timing-v2",
    "rhoticContinuity": {
        "frameSeconds": 0.005,
        "quietSeconds": 0.03,
        "quietAbsoluteRms": 0.0002,
        "quietRelativeRms": 0.0075,
        "scope": "Observed non-syllabic 儿 may share a reliable base only within one acoustically continuous syllable",
    },
}

TIMING_POLICY = {
    **TIMING_POLICY_V2,
    "version": "sensevoice-greedy-character-timing-v3",
    "posteriorIdentity": "Unique raw tokenizer IDs; decoded spellings may coincide",
}


def is_character_timing(evidence):
    return isinstance(evidence, dict) and evidence.get("policy") in (
        TIMING_POLICY_V1, TIMING_POLICY_V2, TIMING_POLICY,
    )


def canonical_samples(samples, sample_rate):
    values = np.asarray(samples, dtype=np.float32)
    return np.asarray(resample_poly(values, 16000, sample_rate), dtype="<f4")


def sample_digest(samples, sample_rate=16000):
    return hashlib.sha256(canonical_samples(samples, sample_rate).tobytes()).hexdigest()


def acoustic_continuity(samples, sample_rate=16000):
    """Retain real qualifying quiet; never fill a gap with an inferred syllable."""
    values = canonical_samples(samples, sample_rate)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("Acoustic continuity requires finite mono samples")
    policy = TIMING_POLICY["rhoticContinuity"]
    frame = round(16000 * policy["frameSeconds"])
    # As in the ending gate, zero-padding only completes the last analysis frame.
    rms = np.sqrt(np.mean(np.pad(values, (0, (-len(values)) % frame))
                          .reshape(-1, frame) ** 2, axis=1))
    threshold = max(policy["quietAbsoluteRms"],
                    float(np.percentile(rms, 90)) * policy["quietRelativeRms"])
    minimum = math.ceil(policy["quietSeconds"] / policy["frameSeconds"])
    spans, first = [], None
    for index, quiet in enumerate(rms <= threshold):
        if quiet and first is None:
            first = index
        if not quiet and first is not None:
            if index - first >= minimum:
                spans.append({"start": first * frame / 16000,
                              "end": index * frame / 16000})
            first = None
    if first is not None and len(rms) - first >= minimum:
        spans.append({"start": first * frame / 16000, "end": len(values) / 16000})
    return {"quietThreshold": threshold, "quietSpans": spans}


def verify_timing_waveform(samples, sample_rate, evidence):
    if is_character_timing(evidence):
        if evidence.get("pcmSha256") != sample_digest(samples, sample_rate):
            raise ValueError("Timing evidence belongs to different audio samples")
        if (evidence["policy"] in (TIMING_POLICY_V2, TIMING_POLICY)
                and evidence.get("acousticContinuity") != acoustic_continuity(samples, sample_rate)):
            raise ValueError("Acoustic continuity does not match the audio samples")


class CharacterTimingRecognizer:
    def __init__(self, model):
        self.model = model

    def recognize(self, samples):
        values = np.asarray(samples, dtype=np.float32)
        duration = len(values) / 16000
        if (values.ndim != 1 or not np.isfinite(values).all()
                or not 0 < duration <= TIMING_POLICY["maximumSeconds"]):
            raise ValueError("Character timing requires finite mono audio up to 30 seconds")
        capture = []

        def observe(module, arguments, logits):
            probabilities = logits.detach().float().softmax(-1)[0]
            confidence, indices = probabilities.topk(8, dim=-1)
            capture.append((confidence.cpu().tolist(), indices.cpu().tolist()))

        handle = self.model.model.ctc.ctc_lo.register_forward_hook(observe)
        try:
            result = self.model.generate(input=values, cache={}, language="zh", use_itn=False,
                                         batch_size_s=60, disable_pbar=True)
        finally:
            handle.remove()
        if len(capture) != 1:
            raise ValueError("Expected one complete ASR observation")
        probabilities, indices = capture[0]
        tokens, previous = [], None
        prefix = TIMING_POLICY["metadataFrames"]
        for frame, (candidate_ids, candidate_probabilities) in enumerate(zip(indices[prefix:], probabilities[prefix:])):
            index, probability = candidate_ids[0], candidate_probabilities[0]
            alternatives = [{"tokenId": i, "text": self.model.kwargs["tokenizer"].decode([i]), "probability": p}
                            for i, p in zip(candidate_ids, candidate_probabilities)]
            start = max(0.0, frame * 0.06 - 0.03)
            end = min(duration, (frame + 1) * 0.06 - 0.03)
            if index != previous and index != self.model.model.blank_id:
                tokens.append({"tokenId": index, "text": self.model.kwargs["tokenizer"].decode([index]),
                               "start": start, "end": end, "probability": probability,
                               "alternatives": alternatives})
            elif index == previous and index != self.model.model.blank_id:
                tokens[-1]["end"] = end
                if probability > tokens[-1]["probability"]:
                    tokens[-1]["probability"] = probability
                    tokens[-1]["alternatives"] = alternatives
            previous = index
        return {"policy": TIMING_POLICY, "durationSeconds": duration,
                "pcmSha256": sample_digest(values), "tokens": tokens,
                "acousticContinuity": acoustic_continuity(values),
                "transcript": re.sub(r"<\|.*?\|>", "", result[0].get("text", ""))}


@lru_cache(maxsize=8192)
def _spellings(character):
    from pypinyin import Style, pinyin
    from pypinyin.contrib.tone_convert import to_tone3

    readings = set(pinyin(character, style=Style.TONE, heteronym=True)[0])
    if character == "期":
        readings.add("qí")  # Same narrow observed-spelling allowance as the content gate.
    return {to_tone3(value, neutral_tone_with_five=True).replace("v", "ü") for value in readings}


def character_observations(text, pinyin, evidence, duration):
    from scripts.audio_pipeline.phonetic import expected_readings
    from scripts.audio_pipeline.quality import transcript_matches

    if not is_character_timing(evidence):
        raise ValueError("Missing pinned character timing policy")
    tokens = evidence.get("tokens", [])
    expected = expected_readings(text, pinyin, character_ends=True)
    if not expected:
        raise ValueError("Authored readings cannot be mapped to characters")
    transcript = "".join(token["text"] for token in tokens)
    if (len(tokens) != expected[1][-1]
            or any(not re.fullmatch(r"[\u3400-\u9fff]", token["text"]) for token in tokens)
            or transcript != evidence.get("transcript")
            or not transcript_matches(text, transcript, pinyin)):
        raise ValueError("Observed characters do not cover the complete authored utterance")
    recorded = evidence.get("durationSeconds", 0)
    if not math.isfinite(recorded) or abs(recorded - duration) > 0.0001:
        raise ValueError("Timing evidence belongs to a different duration")
    intended = []
    for choices, end in zip(*expected):
        intended.append(choices)
        while len(intended) < end:
            intended.append({"r"})
    observations, previous = [], 0.0
    for position, token in enumerate(tokens):
        start, end, probability = token["start"], token["end"], token["probability"]
        if (not all(math.isfinite(v) for v in (start, end, probability))
                or not previous <= start < end <= duration or not 0 <= probability <= 1):
            raise ValueError("Invalid character timing observation")
        # Orthographic posterior mass splits between homophones such as 她/他.
        # At the same observed frame, count only alternatives that independently
        # satisfy the unchanged full-utterance content rule. Timing comes from the
        # greedy event, never from authored forced alignment or a repaired decode.
        id_aware = evidence["policy"] == TIMING_POLICY
        if id_aware and (type(token.get("tokenId")) is not int or token["tokenId"] < 0):
            raise ValueError("Invalid greedy tokenizer ID")
        alternatives = token.get("alternatives", [{"tokenId": token.get("tokenId"),
                                                   "text": token["text"], "probability": probability}])
        total = 0.0
        seen = set()
        mass = 0.0
        for alternative in alternatives:
            value, confidence = alternative["text"], alternative["probability"]
            identity = alternative.get("tokenId") if id_aware else value
            if id_aware and (type(identity) is not int or identity < 0):
                raise ValueError("Invalid posterior tokenizer ID")
            # Distinct SentencePiece IDs can decode to the same surface (for
            # example, the ordinary and word-initial spellings of English be).
            # They are distinct outcomes in the raw softmax distribution. Keep
            # each probability exactly once; text uniqueness is not ID uniqueness.
            if (identity in seen or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("Invalid posterior alternatives")
            seen.add(identity)
            total += confidence
            if re.fullmatch(r"[\u3400-\u9fff]", value):
                readings = _spellings(value)
                equivalent = (value == token["text"] or bool(readings & intended[position])
                              or any(choice.endswith("5") and choice[:-1] == reading[:-1]
                                     for choice in intended[position] for reading in readings))
                if equivalent:
                    mass += confidence
        if (total > 1.00001 or not 1 <= len(alternatives) <= 8
                or alternatives[0]["text"] != token["text"]
                or alternatives[0]["probability"] != probability
                or (id_aware and alternatives[0]["tokenId"] != token["tokenId"])
                or any(a["probability"] < b["probability"] for a, b in zip(alternatives, alternatives[1:]))):
            raise ValueError("Invalid posterior distribution")
        observations.append({**token, "reliable": mass + 1e-8 >= TIMING_POLICY["minimumPeakProbability"]})
        previous = end
    # A written non-syllabic 儿 can have its own orthographic CTC event.
    # Group both observed events under the authored syllable, retaining their
    # full span. Missing events are never invented or interpolated.
    grouped, first = [], 0
    for end in expected[1]:
        group = observations[first:end]
        reliable = all(row["reliable"] for row in group)
        if (not reliable and evidence["policy"] in (TIMING_POLICY_V2, TIMING_POLICY)
                and len(group) == 2 and group[0]["reliable"]
                and group[-1]["text"] == "儿" and intended[end - 1] == {"r"}):
            continuity = evidence.get("acousticContinuity")
            if not isinstance(continuity, dict) or not isinstance(continuity.get("quietSpans"), list):
                raise ValueError("Rhotic grouping requires observed acoustic continuity")
            threshold = continuity.get("quietThreshold", float("nan"))
            if not math.isfinite(threshold) or threshold < TIMING_POLICY["rhoticContinuity"]["quietAbsoluteRms"]:
                raise ValueError("Invalid acoustic quiet threshold")
            # A weak written suffix must not move the ending/noise boundary over
            # real quiet into a detached burst. Waveform consumers recompute this
            # entire certificate, in addition to verifying the PCM hash.
            reliable, previous_quiet = True, 0.0
            for span in continuity["quietSpans"]:
                start, finish = span["start"], span["end"]
                if (not all(math.isfinite(v) for v in (start, finish))
                        or not previous_quiet <= start < finish <= duration):
                    raise ValueError("Invalid acoustic quiet span")
                overlap = min(finish, group[-1]["end"]) - max(start, group[0]["start"])
                if overlap + 1e-8 >= TIMING_POLICY["rhoticContinuity"]["quietSeconds"]:
                    reliable = False
                previous_quiet = finish
        grouped.append({"start": group[0]["start"], "end": group[-1]["end"],
                        "reliable": reliable})
        first = end
    return grouped, expected[1]
