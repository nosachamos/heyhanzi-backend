"""Pinned, observable CosyVoice adapter for finite offline generation."""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class InvalidGeneration(RuntimeError):
    """A completed model attempt produced too few tokens to render audio."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def lexical_targets(entry):
    """Return only explicitly authored isolated or quoted lexical readings."""
    match = re.fullmatch(r"([\u3400-\u9fff])([。！？!?]?)", entry["text"])
    if match and entry.get("pinyin"):
        return [{"text": match[1], "pinyin": entry["pinyin"]}]
    if entry.get("practiceWord"):
        return [{"text": entry["practiceWord"], "pinyin": entry["practicePinyin"]}]
    if entry.get("kind") == "word-practice":
        isolated = re.fullmatch(r"([\u3400-\u9fff]+)。", entry["text"])
        quoted = re.fullmatch(r"我在学“([\u3400-\u9fff]+)”。", entry["text"])
        if isolated:
            return [{"text": isolated[1], "pinyin": entry["pinyin"]}]
        if quoted and entry["pinyin"].startswith("wǒ zài xué "):
            return [{"text": quoted[1], "pinyin": entry["pinyin"].removeprefix("wǒ zài xué ")}]
    return entry.get("quotedReadings", [])


def reading_key(value):
    return "".join(c for c in value.split("/")[0].lower() if c.isalpha()).replace("v", "ü")


def native_alternative(entry):
    """Native spelling must preserve the selected lexical reading.

    A compound may naturally neutralize a syllable (地方 dìfang). A single
    character's nondefault reading, such as 地 de, must retain explicit control.
    """
    from functools import lru_cache

    from pypinyin import Style, lazy_pinyin
    from pypinyin.contrib.tone_convert import to_normal

    targets = lexical_targets(entry)
    if not targets:
        return False
    for target in targets:
        intended = reading_key(target["pinyin"])
        defaults = lazy_pinyin(target["text"], style=Style.TONE)
        if len(target["text"]) == 1:
            if intended != defaults[0]:
                return False
            continue

        @lru_cache(maxsize=None)
        def matches(index, offset):
            if index == len(defaults):
                return offset == len(intended)
            return any(
                intended.startswith(value, offset) and matches(index + 1, offset + len(value))
                for value in {defaults[index], to_normal(defaults[index])}
            )

        if not matches(0, 0):
            return False
    return True


def replace_lexical_target(text, target, replacement):
    word = target["text"]
    if re.fullmatch(re.escape(word) + r"[。！？!?]?", text):
        return replacement + text[len(word) :]
    result = text
    for left, right in [("“", "”"), ("「", "」")]:
        result = result.replace(left + word + right, left + replacement + right)
    if result == text and replacement != word:
        raise ValueError("Lexical alternative has no matching isolated/quoted target")
    return result


def input_variants(entry):
    targets = lexical_targets(entry)
    variants = []
    if native_alternative(entry):
        variants.append("native")
    variants.append("phonemes")
    if targets and all(
        len(t["text"]) == 1 and re.fullmatch(r"[a-z]+", reading_key(t["pinyin"])) for t in targets
    ):
        variants.append("romanized-neutral")
        if all(reading_key(t["pinyin"]) == "de" for t in targets) and any(
            t["text"] != "的" for t in targets
        ):
            variants.append("neutral-homophone")
    if targets and any(
        left + t["text"] + right in entry["text"]
        for t in targets
        for left, right in [("“", "”"), ("「", "」")]
    ):
        variants.append("phoneme-boundary")
        if "native" in variants:
            variants.append("quoted-boundary")
    if any(target["text"] == "乒" for target in targets):
        variants.remove("phonemes")
        variants.insert(0, "phonemes")
    if targets and any(
        left + target["text"] + right in entry["text"]
        for target in targets
        for left, right in [("“", "”"), ("「", "」")]
    ):
        variants = (
            (["unquoted-native"] if "native" in variants else []) + ["unquoted-phonemes"] + variants
        )
    if len(entry["text"].rstrip("。！？!?")) == 1 and targets:
        variants.append("quoted-isolate-phonemes")
    return variants


class Synthesizer:
    def __init__(self, source, weights, profile, reference, threads=2):
        source, weights, reference = Path(source), Path(weights), Path(reference)
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        for options in [[], ["--cached"]]:
            if subprocess.run(["git", "-C", str(source), "diff", "--quiet", *options]).returncode:
                raise ValueError("Pinned model source has unrecorded tracked changes")
        weights_revision = (
            (weights / ".cache/huggingface/download/llm.pt.metadata").read_text().splitlines()[0]
        )
        if (
            revision != profile["codeRevision"]
            or weights_revision != profile["modelWeightsRevision"]
        ):
            raise ValueError("Model source/weights do not match the pinned recipe")
        if (
            hashlib.sha256(reference.read_bytes()).hexdigest()
            != profile["referenceVoice"]["sha256"]
        ):
            raise ValueError("Reference checksum mismatch")
        sys.path[:0] = [str(source), str(source / "third_party/Matcha-TTS")]
        import torch
        from cosyvoice.cli.cosyvoice import AutoModel

        if not torch.cuda.is_available():
            raise RuntimeError("Offline generation requires the configured CUDA GPU")
        torch.set_num_threads(threads)
        torch.set_float32_matmul_precision("highest")
        self.model = AutoModel(model_dir=str(weights), fp16=False)
        self.profile, self.reference = profile, str(reference)
        self.sample_rate = self.model.sample_rate
        self.trace = {}
        self.cached_prompts = {}
        # Pinned upstream wrapper yields each speech token and returns on EOS.
        # Observing it at this boundary distinguishes EOS from token exhaustion.
        original = self.model.model.llm.inference_wrapper

        def observed(lm_input, sampling, min_len, max_len, uuid):
            self.request_uuid = uuid
            cap = min(2250, max(120, int(max_len) * 2))
            count = 0
            self.trace = {
                "reason": "interrupted",
                "generatedSpeechTokens": 0,
                "maximumSpeechTokens": cap,
            }
            for token in original(lm_input, sampling, min_len, cap, uuid):
                count += 1
                yield token
            self.trace = {
                "reason": "limit" if count >= cap else "eos",
                "generatedSpeechTokens": count,
                "maximumSpeechTokens": cap,
            }

        self.model.model.llm.inference_wrapper = observed
        self.set_mode(profile["mode"])

    def set_mode(self, mode, entry=None):
        self.mode = mode
        if mode not in {"zero_shot", "instruct2", "clear", "cross_lingual"}:
            raise ValueError("Unknown synthesis mode")
        if mode == "clear" and not entry:
            raise ValueError("Clear-reading mode needs the authored pronunciation")
        clear_instruction = self.profile["clearInstruction"]
        if mode == "clear":
            for target in lexical_targets(entry):
                selected = self.profile.get("clearInstructionByTarget", {}).get(target["text"])
                if selected:
                    clear_instruction = selected
                    break
        prompt = (
            clear_instruction.format(pinyin=entry["pinyin"])
            if mode == "clear"
            else (
                "You are a helpful assistant.<|endofprompt|>"
                + self.profile["referenceVoice"]["transcript"]
                if mode in {"zero_shot", "cross_lingual"}
                else self.profile["instruction"]
            )
        )
        if self.cached_prompts.get(mode) != prompt:
            # Keep at most one embedding per mode, including per-item guidance.
            self.model.frontend.spk2info.pop(mode, None)
            self.model.add_zero_shot_spk(prompt, self.reference, mode)
            self.cached_prompts[mode] = prompt
        self.prompt = prompt

    def generate(self, entry, seed, native=False, romanized=False, variant=None):
        import torch
        from cosyvoice.utils.common import set_all_random_seed

        from scripts.generate_sentence_audio import pronunciation_input

        set_all_random_seed(seed)
        native_text = entry["text"]
        if native and romanized:
            raise ValueError("Choose only one lexical input alternative")
        if variant is not None and (native or romanized):
            raise ValueError("Choose a named input variant or legacy flags, not both")
        variant = variant or (
            "native" if native else "romanized-neutral" if romanized else "phonemes"
        )
        if variant not in input_variants(entry):
            raise ValueError("Input alternative does not preserve the authored lexical reading")
        targets = lexical_targets(entry)
        if entry.get("kind") == "word-practice" and targets and not entry.get("practiceWord"):
            entry = {
                **entry,
                "practiceWord": targets[0]["text"],
                "practicePinyin": targets[0]["pinyin"],
            }
        standalone = re.fullmatch(r"([\u3400-\u9fff])([。！？!?]?)", entry["text"])
        original_punctuation = None
        if standalone and entry.get("pinyin"):
            entry = dict(entry)
            original_punctuation = standalone[2]
            entry.update(
                text=standalone[1] + "。",
                practiceWord=standalone[1],
                practicePinyin="".join(c for c in entry["pinyin"].split("/")[0] if c.isalpha()),
            )
        text, overrides = pronunciation_input(
            entry, self.model.frontend.tokenizer.special_tokens["additional_special_tokens"]
        )
        if original_punctuation is not None:
            text = text.removesuffix("。") + original_punctuation
        if variant == "quoted-isolate-phonemes":
            text = "“" + text.rstrip("。！？!?") + "”。"
            overrides.append({"mode": variant})
        if variant in {"native", "unquoted-native"}:
            text = native_text
            overrides = [{"mode": "native-default-reading", "pinyin": entry["pinyin"]}]
        if variant in {"romanized-neutral", "neutral-homophone"}:
            text = native_text
            overrides = []
            for target in targets:
                replacement = (
                    "的" if variant == "neutral-homophone" else reading_key(target["pinyin"])
                )
                text = replace_lexical_target(text, target, replacement)
                overrides.append({**target, "mode": variant, "replacement": replacement})
        if variant == "phoneme-boundary":
            text = text.replace("“", "，").replace("”", "").replace("「", "，").replace("」", "")
            text = re.sub(r"([，。！？：；])，", r"\1", text).lstrip("，")
            overrides.append({"mode": variant})
        if variant == "quoted-boundary":
            text = native_text
            for target in targets:
                for left, right in [("“", "”"), ("「", "」")]:
                    text = text.replace(left + target["text"] + right, "，" + target["text"])
            text = re.sub(r"([，。！？：；])，", r"\1", text).lstrip("，")
            overrides = [{"mode": variant, "targets": targets}]
        if variant in {"unquoted-native", "unquoted-phonemes"}:
            text = text.translate(str.maketrans("", "", "“”「」"))
            overrides.append({"mode": variant})
        self.trace = {"reason": "interrupted"}
        if self.mode == "cross_lingual":
            # Upstream's unprompted text mode retains this same speaker's acoustic
            # conditioning, without transcript/instruction conditioning in the LLM.
            text = "You are a helpful assistant.<|endofprompt|>" + text
            output = self.model.inference_cross_lingual(
                text,
                self.reference,
                zero_shot_spk_id=self.mode,
                stream=False,
                speed=1.0,
                text_frontend=False,
            )
        elif self.mode == "zero_shot":
            output = self.model.inference_zero_shot(
                text,
                self.prompt,
                self.reference,
                zero_shot_spk_id=self.mode,
                stream=False,
                speed=1.0,
                text_frontend=False,
            )
        else:
            output = self.model.inference_instruct2(
                text,
                self.prompt,
                self.reference,
                zero_shot_spk_id=self.mode,
                stream=False,
                speed=1.0,
                text_frontend=False,
            )
        try:
            parts = list(output)
        except RuntimeError as error:
            # Upstream can emit EOS before enough tokens exist for its vocoder.
            # Count this as a failed candidate, but propagate unrelated GPU/errors.
            short_output = (
                self.trace.get("reason") == "eos"
                and self.trace.get("generatedSpeechTokens", 999) < 4
                and "Kernel size can't be greater than actual input size" in str(error)
            )
            if not short_output:
                raise
            # The non-streaming upstream job has joined before vocoding; its
            # normal cleanup is skipped when token2wav raises.
            with self.model.model.lock:
                for name in ["tts_speech_token_dict", "llm_end_dict", "hift_cache_dict"]:
                    getattr(self.model.model, name).pop(self.request_uuid, None)
            raise InvalidGeneration("Too few generated tokens for the vocoder") from error
        if not parts:
            raise InvalidGeneration("The model returned no audio")
        waveform = torch.cat([p["tts_speech"] for p in parts], dim=1).cpu().numpy().ravel()
        return waveform, dict(self.trace), text, overrides
