"""Compatibility entrypoint and lexical controls for offline audio production.

The legacy forced-slow generator has been retired. Use the qualified two-voice
pipeline; this entrypoint accepts the old source/model flag spellings.
"""
import re
import sys
from functools import lru_cache


def pronunciation_input(entry, available_tokens):
    """Use the existing CosyVoice pinyin controls for context-free readings.

    Ordinary connected sentences stay Chinese text, allowing natural sandhi.
    A quoted/isolated polyphone needs its explicit curricular lexical reading.
    """
    initials = ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h",
                "j", "q", "x", "r", "z", "c", "s", "y", "w", "")
    finals = {token[1:-1] for token in available_tokens if re.fullmatch(
        r"\[[aeiouüāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ][a-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]*\]", token)}
    finals.add("vè")  # CosyVoice's spelling for the üè final in 略/虐.
    mapping = {initial + final: (f"[{initial}]" if initial else "") + f"[{final}]"
               for initial in initials for final in finals}
    mapping["r"] = "[r]"

    @lru_cache(maxsize=256)
    def parse(value):
        if not value:
            return ""
        for length in range(min(7, len(value)), 0, -1):
            head = value[:length]
            if head in mapping:
                tail = parse(value[length:])
                if tail is not None:
                    return mapping[head] + tail
        return None

    def preserve_sandhi_prefix(word, sound, phonemes):
        # Citation yī/bù at the start of a compound needs connected-speech
        # sandhi. Keep that unambiguous Hanzi native while controlling the
        # following lexical reading (for example 一[q][í], not [y][ī][q][í]).
        if len(word) > 1:
            for character, reading, tokens in [("一", "yī", "[y][ī]"), ("不", "bù", "[b][ù]")]:
                if word.startswith(character) and sound.startswith(reading) and phonemes.startswith(tokens):
                    return character + phonemes[len(tokens):]
        return phonemes

    hints = entry.get("quotedReadings", [])
    if entry.get("practiceWord"):
        hints = [{"text": entry["practiceWord"], "pinyin": entry["practicePinyin"]}]
    text = entry["text"]
    overrides = []
    for hint in hints:
        selected = hint["pinyin"].split("/")[0]
        sound = re.sub(r"[\s'’\-]", "", selected.lower()).replace("üè", "vè")
        if sound == "ǹg":
            # The tokenizer has no syllabic-ng tone token. Keep the actual 嗯,
            # rather than replacing it with a different spoken syllable.
            overrides.append({**hint, "selectedReading": selected, "mode": "native-syllabic-nasal"})
            continue
        phonemes = parse(sound)
        if phonemes is None:
            raise ValueError(f"Unsupported curricular pinyin: {hint['pinyin']}")
        phonemes = preserve_sandhi_prefix(hint["text"], sound, phonemes)
        if text == hint["text"] + "。":
            text = phonemes + "。"
        else:
            for left, right in (("“", "”"), ("「", "」")):
                text = text.replace(left + hint["text"] + right, left + phonemes + right)
        overrides.append({**hint, "selectedReading": selected, "tokens": phonemes})
    # Explicit authored controls for a few contextual readings that the model
    # repeatedly misreads. Offsets refer to the original Mandarin text.
    controls = entry.get("contextualReadings", [])
    if controls and hints:
        raise ValueError("Do not combine contextual and lexical input controls")
    last_start = len(entry["text"])
    for hint in sorted(controls, key=lambda item: item["start"], reverse=True):
        start, end = hint["start"], hint["end"]
        if not 0 <= start < end <= last_start or entry["text"][start:end] != hint["text"]:
            raise ValueError("Invalid contextual reading offset")
        selected = hint["pinyin"].split("/")[0]
        sound = re.sub(r"[\s'’\-]", "", selected.lower()).replace("üè", "vè")
        phonemes = parse(sound)
        if phonemes is None:
            raise ValueError("Unsupported contextual pinyin")
        phonemes = preserve_sandhi_prefix(hint["text"], sound, phonemes)
        text = text[:start] + phonemes + text[end:]
        last_start = start
        overrides.append({**hint, "selectedReading": selected, "tokens": phonemes,
                          "mode": "authored-contextual-reading"})
    return text, overrides



if __name__ == "__main__":
    from scripts.audio_pipeline.run import main
    aliases = {"--cosyvoice-source": "--source", "--model-dir": "--weights"}
    sys.argv = [aliases.get(value, value) for value in sys.argv]
    main()
