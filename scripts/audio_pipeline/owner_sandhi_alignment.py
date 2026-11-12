"""Narrow, measured spelling equivalence for owner-selected word highlighting.

This never qualifies pronunciation or changes an existing receipt. A primary
decoder can spell 游好 as 友好: third+third surfaces as second+third. Require an
independent unprompted observation of those actual adjacent sounds, then use
the original primary event times without editing any observation. A separate
policy covers 一 before a fourth tone, whose surface reading is yi2.
"""
import math
import re

from scripts.audio_pipeline.owner_selection import digest, owner_syllables
from scripts.audio_pipeline.phonetic import PHONETIC_POLICY, expected_readings
from scripts.audio_pipeline.quality import chinese
from scripts.audio_pipeline.timing import _spellings

POLICY = {
    "version": "owner-third-tone-spelling-alignment-v1",
    "maximumEquivalences": 1,
    "minimumPhoneticProbability": .98,
    "maximumStartDifferenceSeconds": .18,
    "maximumEndDifferenceSeconds": .18,
    "scope": "One observed third-tone spelling before another third tone, independently heard as the authored second+third. Alignment only; original failures retained.",
}
YI_POLICY = {
    **POLICY,
    "version": "owner-yi-sandhi-spelling-alignment-v1",
    "scope": "One authored 一 (yi1) before a fourth tone, independently heard as yi2 plus the following fourth tone; excludes ordinals, digit sequences, compound-number endings, weekdays and calendar dates. Alignment only; original failures retained.",
}


def ordinary_yi_context(text, position):
    """Conservatively exclude citation-tone number and calendar contexts."""
    digits = "零〇一二两三四五六七八九0123456789０１２３４５６７８９"
    before, after = text[:position].rstrip(), text[position + 1:].lstrip()
    return (text[position] == "一"
            and not (before and before[-1] in "第十百千万亿" + digits)
            and not (after and after[0] in digits + "月日号")
            and not before.endswith(("星期", "礼拜", "周")))


def phonetic_rows(evidence, duration):
    if (not isinstance(evidence, dict) or evidence.get("policy") != PHONETIC_POLICY
            or not math.isfinite(evidence.get("durationSeconds", float("nan")))
            or abs(evidence["durationSeconds"] - duration) > .0001):
        raise ValueError("Sandhi supplement requires pinned independent phonetic observations of this duration")
    rows, pending, previous = [], [], 0.0
    for token in evidence.get("tokens", []):
        text, start, end, probability = (token["text"], token["start"], token["end"], token["probability"])
        if (not re.fullmatch(r"[a-züv]+|[1-5]", text)
                or not all(math.isfinite(v) for v in (start, end, probability))
                or not previous <= start < end <= duration or not 0 <= probability <= 1):
            raise ValueError("Invalid independent phonetic event")
        previous = end
        pending.append(token)
        if text in {"1", "2", "3", "4", "5"}:
            if len(pending) < 2:
                # Keep an orphan observation as a barrier. It cannot complete
                # a syllable or join the complete sounds on either side.
                rows.append(None)
            else:
                rows.append({"reading": "".join(t["text"] for t in pending).replace("v", "ü"),
                             "start": pending[0]["start"], "end": end,
                             "probability": min(t["probability"] for t in pending)})
            pending = []
    if pending:
        rows.append(None)
    if not any(row is not None for row in rows):
        raise ValueError("No complete independent phonetic event")
    return rows


def owner_sandhi_syllables(text, pinyin, primary_evidence, duration, phonetic_evidence):
    """Return original measured syllable spans plus an auditable supplement.

    The caller verifies the MP3/PCM binding and retains both original observers.
    Only a comparison spelling/reading is changed locally, never an evidence
    token, word, timestamp, audio sample or automatic qualification result.
    """
    from pypinyin import Style, lazy_pinyin
    from pypinyin.contrib.tone_convert import to_tone

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid sandhi supplement duration")
    try:
        owner_syllables(text, pinyin, primary_evidence, duration)
    except ValueError:
        pass
    else:
        raise ValueError("Original owner timing already covers this utterance")
    expected = expected_readings(text, pinyin, character_ends=True)
    if not expected:
        raise ValueError("Cannot map authored readings")
    choices, character_ends = expected
    starts = [0, *character_ends[:-1]]
    characters = chinese(text)
    character_positions = [index for index, character in enumerate(text) if chinese(character)]
    contextual = lazy_pinyin(characters, style=Style.TONE3, neutral_tone_with_five=True)
    comparison_readings = []
    for options, start, end in zip(choices, starts, character_ends):
        usual = contextual[start].replace("v", "ü")
        if end - start == 2 and characters[end - 1] == "儿":
            usual = usual[:-1] + "r" + usual[-1]
        # The parser's alternatives differ only by normal sandhi; choose the
        # contextual citation when present, otherwise an actual authored choice.
        comparison_readings.append(usual if usual in options else sorted(options)[0])
    observed = primary_evidence.get("tokens", [])
    independent = phonetic_rows(phonetic_evidence, duration)
    matches = []
    for index in range(len(choices) - 1):
        start, end = starts[index], character_ends[index]
        if end - start != 1 or character_ends[index + 1] - end != 1:
            continue
        # Sandhi equivalence is a local spoken pair, not a license to bridge
        # an authored punctuation/prosodic boundary that chinese() removes.
        if text[character_positions[start] + 1:character_positions[end]].strip():
            continue
        patterns = [(authored, authored[:-1] + "3", following, authored, POLICY)
                    for authored in sorted(choices[index]) if authored.endswith("2")
                    for following in sorted(choices[index + 1]) if following.endswith("3")]
        if (characters[start] == "一" and {"yi1", "yi2"} <= choices[index]
                and ordinary_yi_context(text, character_positions[start])):
            patterns.extend(("yi1", "yi2", following, "yi2", YI_POLICY)
                            for following in sorted(choices[index + 1]) if following.endswith("4"))
        for authored, citation, following, surface, policy in patterns:
            for event_index, (left, right) in enumerate(zip(observed, observed[1:])):
                if (left.get("text") == characters[start]
                        or citation not in _spellings(left.get("text", ""))
                        or following not in _spellings(right.get("text", ""))):
                    continue
                for heard, next_heard in zip(independent, independent[1:]):
                    if (heard is None or next_heard is None
                            or heard["reading"] != surface or next_heard["reading"] != following
                            or min(heard["probability"], next_heard["probability"]) < POLICY["minimumPhoneticProbability"]
                            or any(abs(event["start"] - sound["start"]) > POLICY["maximumStartDifferenceSeconds"]
                                   or abs(event["end"] - sound["end"]) > POLICY["maximumEndDifferenceSeconds"]
                                   for event, sound in [(left, heard), (right, next_heard)])):
                        continue
                    comparison = characters[:start] + left["text"] + characters[start + 1:]
                    readings = comparison_readings.copy()
                    readings[index] = citation
                    comparison_pinyin = " ".join(to_tone(value) for value in readings)
                    try:
                        rows, ends = owner_syllables(comparison, comparison_pinyin, primary_evidence, duration)
                    except ValueError:
                        continue
                    if ends != character_ends or rows[index]["start"] != left["start"] or rows[index]["end"] != left["end"]:
                        continue
                    rows[index] = {**rows[index], "reliable": False, "sandhiSpellingEquivalent": True}
                    equivalence = {"authoredCharacterIndex": start, "observedEventIndex": event_index,
                        "authoredCharacter": characters[start], "observedCharacter": left["text"],
                        "authoredReading": authored, "observedCitationReading": citation,
                        "followingReading": following, "primaryStart": left["start"], "primaryEnd": left["end"],
                        "phoneticStart": heard["start"], "phoneticEnd": heard["end"],
                        "followingPhoneticStart": next_heard["start"], "followingPhoneticEnd": next_heard["end"]}
                    supplement = {"policy": policy, "primaryEvidenceSha256": digest(primary_evidence),
                        "phoneticEvidenceSha256": digest(phonetic_evidence), "durationSeconds": duration,
                        "equivalences": [equivalence], "comparisonText": comparison,
                        "comparisonPinyin": comparison_pinyin,
                        "qualification": "owner-selected alignment only; original failures retained"}
                    matches.append((rows, ends, supplement))
    if len(matches) != 1:
        raise ValueError("No unique independently confirmed sandhi spelling equivalence")
    return matches[0]
