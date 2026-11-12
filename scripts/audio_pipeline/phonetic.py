"""Independent unprompted phonetic evidence for ambiguous orthographic ASR.

Offline only. Never feed the authored transcript into the recognizer. A fallback
must match every syllable and lexical tone, allowing only contextual sandhi, with
high-confidence CTC tokens. It cannot certify a native-speaker judgment.
"""

import hashlib
import importlib.util
import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

PHONETIC_POLICY = json.loads(Path(__file__).with_name("phonetic-model.json").read_text())


def expected_readings(text, intended_pinyin, *, character_ends=False):
    from pypinyin import Style, lazy_pinyin, pinyin
    from pypinyin.contrib.tone_convert import to_normal, to_tone3

    characters = "".join(re.findall(r"[\u3400-\u9fff]", text))
    sounds = "".join(c for c in intended_pinyin.lower().split("/")[0] if c.isalpha()).replace("v", "ü")
    contextual = lazy_pinyin(characters, style=Style.TONE)

    @lru_cache(maxsize=None)
    def align(index, offset):
        if index == len(characters):
            return () if offset == len(sounds) else None
        choices = set(pinyin(characters[index], style=Style.TONE, heteronym=True)[0]) | {contextual[index]}
        choices |= {to_normal(value) for value in choices}
        if characters[index] == "儿":
            choices.add("r")
        for value in sorted(choices, key=lambda s: (-len(s), s)):
            if sounds.startswith(value, offset):
                tail = align(index + 1, offset + len(value))
                if tail is not None:
                    return (value,) + tail
        return None

    values = align(0, 0)
    if values is None:
        return None
    result, ends = [], []
    for index, value in enumerate(values):
        if value == "r":
            if not result:
                return None
            result[-1] = {v[:-1] + "r" + v[-1] for v in result[-1]}
            ends[-1] = index + 1
            continue
        number = to_tone3(value, neutral_tone_with_five=True).replace("v", "ü")
        choices = {number}
        following = (
            to_tone3(values[index + 1], neutral_tone_with_five=True)
            if index + 1 < len(values) and values[index + 1] != "r"
            else ""
        )
        if number.endswith("3") and following.endswith("3"):
            choices.add(number[:-1] + "2")
        if characters[index] == "一" and number == "yi1" and following:
            choices.add("yi2" if following.endswith("4") else "yi4")
        if characters[index] == "不" and number == "bu4" and following.endswith("4"):
            choices.add("bu2")
        result.append(choices)
        ends.append(index + 1)
    return (result, ends) if character_ends else result


def phonetic_matches(text, pinyin, evidence):
    if not evidence or evidence.get("policy") != PHONETIC_POLICY:
        return False
    tokens = evidence.get("tokens", [])
    duration = evidence.get("durationSeconds", 0)
    if not tokens or not math.isfinite(duration) or not 0 < duration <= PHONETIC_POLICY["maximumSeconds"]:
        return False
    observed = []
    syllable = ""
    previous = 0.0
    for token in tokens:
        confidence, start, end = token["probability"], token["start"], token["end"]
        if (
            not all(math.isfinite(v) for v in [confidence, start, end])
            or not PHONETIC_POLICY["minimumTokenProbability"] <= confidence <= 1
            or not previous <= start < end <= duration + 0.15
        ):
            return False
        previous = end
        syllable += token["text"].replace("v", "ü")
        if token["text"] in {"1", "2", "3", "4", "5"}:
            observed.append(syllable)
            syllable = ""
    if syllable:
        return False
    expected = expected_readings(text, pinyin)
    return expected is not None and len(expected) == len(observed) and all(
        actual in choices for actual, choices in zip(observed, expected)
    )


def phonetic_review_matches(text, pinyin, transcript, evidence):
    """Resolve only small orthographic ambiguities, never unrelated/missing speech."""
    from pypinyin import Style
    from pypinyin import pinyin as character_readings

    expected = "".join(re.findall(r"[\u3400-\u9fff]", text))
    # SenseVoice sometimes spells an isolated Mandarin syllable as its
    # acoustically corresponding Latin letter (e.g. b for bì). Support only
    # these four observed letter names, with every other Hanzi unchanged and
    # independent exact full-utterance tone evidence. Never discard a letter,
    # accept an English word, or infer a tone from the letter itself.
    if re.search(r"[A-Za-z]", transcript):
        names = {"b": "bi", "p": "pi", "d": "di", "t": "ti"}
        units = re.findall(r"[\u3400-\u9fff]|[A-Za-z]+|[0-9]+", transcript)
        readings = expected_readings(text, pinyin)
        if (len(units) != len(expected) or readings is None
                or len(readings) != len(expected)
                or sum(bool(re.fullmatch(r"[A-Za-z]+", unit)) for unit in units) != 1):
            return False
        for index, (character, unit) in enumerate(zip(expected, units)):
            if unit == character:
                continue
            if unit.lower() not in names or {
                value[:-1] for value in readings[index]
            } != {names[unit.lower()]}:
                return False
        return phonetic_matches(text, pinyin, evidence)
    actual = "".join(re.findall(r"[\u3400-\u9fff]", transcript))
    # The Mandarin decoder systematically expands isolated dī into 第一.
    # Resolve this measured spelling artifact only when the independent CTC
    # hears exactly ONE di1 syllable. Actual dì-yī, dì-di and dì recordings were
    # checked as counterexamples; none may pass this gate. Other inserted or
    # missing primary-ASR syllables remain disallowed.
    if (len(expected) == 1 and actual == "第一"
            and expected_readings(text, pinyin) == [{"di1"}]):
        return phonetic_matches(text, pinyin, evidence)
    if not actual or len(expected) != len(actual):
        return False
    changed = [(left, right) for left, right in zip(expected, actual) if left != right]
    if not 1 <= len(changed) <= 2:
        return False
    nasal_changes = 0
    for left, right in changed:
        a = set(character_readings(left, style=Style.NORMAL, heteronym=True)[0])
        b = set(character_readings(right, style=Style.NORMAL, heteronym=True)[0])
        if a & b:
            continue
        if any((x.endswith("n") and x + "g" == y) or (y.endswith("n") and y + "g" == x)
               for x in a for y in b):
            nasal_changes += 1
            continue
        return False
    return nasal_changes <= 1 and phonetic_matches(text, pinyin, evidence)


class PhoneticRecognizer:
    def __init__(self, directory):
        import torch

        directory = Path(directory)
        source = directory / "source/whisper"
        model = directory / "model"
        code = b"".join(
            str(p.relative_to(source)).encode() + b"\0" + p.read_bytes()
            for p in sorted(source.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        )
        checkpoint = model / "checkpoint-epoch=0009.ckpt"
        tokens = model / "tokens.txt"
        if (
            hashlib.sha256(code).hexdigest() != PHONETIC_POLICY["sourceSha256"]
            or hashlib.sha256(checkpoint.read_bytes()).hexdigest() != PHONETIC_POLICY["checkpointSha256"]
            or hashlib.sha256(tokens.read_bytes()).hexdigest() != PHONETIC_POLICY["tokensSha256"]
        ):
            raise ValueError("Phonetic review model/source checksum mismatch")
        # Use an isolated module namespace: CosyVoice also imports Whisper.
        name = "heyhanzi_phonetic_whisper"
        spec = importlib.util.spec_from_file_location(
            name, source / "__init__.py", submodule_search_locations=[str(source)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        architecture = sys.modules[name + ".model"]
        self.audio = sys.modules[name + ".audio"]
        saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
        state = {k.removeprefix("model."): v for k, v in saved["state_dict"].items()}
        self.encoder = architecture.AudioEncoder(80, 1500, 768, 12, 12).eval()
        self.head = architecture.CTCHead(hidden_size=768, vocab_size=280, num_layers=2).eval()
        for prefix, network in [("encoder.", self.encoder), ("ctc_head.", self.head)]:
            network.load_state_dict(
                {k.removeprefix(prefix): v for k, v in state.items() if k.startswith(prefix)},
                strict=True,
            )
        self.vocabulary = {
            int(line.split()[1]): line.split()[0]
            for line in tokens.read_text().splitlines() if line.strip()
        }

    def recognize(self, samples):
        import torch

        samples = np.asarray(samples, dtype=np.float32)
        duration = len(samples) / 16000
        if samples.ndim != 1 or not np.isfinite(samples).all() or not 0 < duration <= 30:
            return None
        mel = self.audio.log_mel_spectrogram(self.audio.pad_or_trim(samples), n_mels=80)
        with torch.inference_mode():
            features = self.encoder(mel.unsqueeze(0))
            features = features[0] if isinstance(features, tuple) else features
            _, logits = self.head(features)
            probabilities = logits[0].softmax(-1)
            indices = probabilities.argmax(-1).tolist()
        tokens = []
        previous = None
        for frame, index in enumerate(indices):
            probability = float(probabilities[frame, index])
            if index != previous and index not in {0, 1}:
                tokens.append({"text": self.vocabulary[index], "start": frame * 0.02,
                               "end": (frame + 1) * 0.02, "probability": probability})
            elif index == previous and index not in {0, 1}:
                tokens[-1]["end"] = (frame + 1) * 0.02
                tokens[-1]["probability"] = max(tokens[-1]["probability"], probability)
            previous = index
        return {"policy": PHONETIC_POLICY, "tokens": tokens, "durationSeconds": duration}
