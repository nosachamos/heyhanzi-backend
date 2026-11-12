"""Apply explicit editorial corrections and conservative sentence-word alignments.

Offline metadata authoring only: never rewrites applied vocabulary migrations.
Run after build_examples.py. Corrections retain upstream IDs and mark changes.
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from app.services.vocabulary_corrections import apply_vocabulary_corrections

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "app/data/learning/examples"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vocabulary", type=Path, required=True)
    args = parser.parse_args()
    lexicon = defaultdict(list)
    for word in apply_vocabulary_corrections(json.loads(args.vocabulary.read_text())["vocabulary"]):
        lexicon[word["text"]].append(word)
    for words in lexicon.values():
        words.sort(key=lambda word: -word["rank"])
    base = json.loads((ROOT / "alembic/data/0012_curriculum.json").read_text())
    warm = json.loads((ROOT / "alembic/data/0004_warmup.json").read_text())["words"]
    lessons = warm + base["characters"]
    singles = {w["text"]: w["pronunciation"]["value"] for w in lessons}
    single_meanings = {w["text"]: w["translations"]["en"] for w in lessons}
    fixes = {}
    for line in (Path(__file__).parent / "mandarin-corrections.tsv").read_text().splitlines():
        if line and not line.startswith("#"):
            old, words, english = line.split("\t")
            fixes[old] = (words.split(), english)
    reading_fixes = {}
    for line in (
        (Path(__file__).parent / "mandarin-reading-corrections.tsv").read_text().splitlines()
    ):
        if line and not line.startswith("#"):
            sentence, word, sound, meaning = line.split("\t")
            reading_fixes[(sentence, word)] = (sound, meaning)
    readings = {
        "有一点儿": "yǒu yì diǎnr",
        "快点儿": "kuài diǎnr",
        "口吃": "kǒu chī",
        "东西": "dōng xi",
        "似是而非": "sì shì ér fēi",
    }
    meanings = {
        "玻": "used in the word for glass",
        "东西": "thing; stuff",
        "家": "home; family",
        "有一点儿": "a little; slightly",
        "快点儿": "hurry up",
        "口吃": "to stammer",
        "似是而非": "seemingly right but actually wrong",
        "没收": "to confiscate",
        "都会": "metropolis",
        "穿着": "clothing; outfit",
        "的": "marks possession or links a description to a noun",
        "着": "marks a continuing action",
        "收": "to receive",
        "好": "good; well",
        "和": "and; with",
    }
    alignment = {
        "我": ["I", "me", "my"],
        "你": ["you", "your"],
        "他": ["he", "him", "his"],
        "她": ["she", "her"],
        "我们": ["we", "us", "our"],
        "他们": ["they", "them", "their"],
        "她们": ["they", "them", "their"],
        "你们": ["you", "your"],
        "是": ["is", "are", "am", "was", "were"],
        "有": ["have", "has", "had"],
        "学": ["study", "studies", "studied", "studying", "learn", "learned", "learning"],
        "说": ["say", "says", "said", "speak", "speaks", "speaking", "spoke"],
        "想": ["want", "wants", "wanted", "think", "thinking", "miss", "misses"],
        "看": ["read", "reads", "reading", "see", "sees", "look", "looking"],
        "老师": ["teacher", "teachers"],
        "好人": ["good person", "good people"],
        "东西": ["thing", "things", "stuff", "something"],
        "去": ["go", "goes", "going", "went"],
        "来": ["come", "comes", "came", "coming"],
    }
    index = json.loads((DATA / "index.json").read_text())
    corrected = 0

    def token(word):
        lexical = lexicon[word][0] if lexicon[word] else None
        sound = readings.get(
            word, singles.get(word, lexical["pronunciation"]["value"] if lexical else "")
        )
        gloss = meanings.get(
            word,
            lexical["translations"]["en"].split(";")[0]
            if lexical
            else single_meanings.get(word, ""),
        )
        gloss = re.sub(r"\([^)]*\)|\[[^]]*\]", "", gloss).strip()
        return {"text": word, "pinyin": sound, "meaning": gloss, "englishRanges": []}

    for filename in index["characters"].values():
        entries = json.loads((DATA / filename).read_text())
        for entry in entries:
            original = entry["source"].get(
                "editorialCorrection", "".join(t["text"] for t in entry["tokens"])
            )
            body = original
            speaker = None
            for candidate in ("她", "我"):
                prefix = candidate + "说：“"
                if original.startswith(prefix) and original.endswith("”"):
                    speaker = candidate
                    body = original[len(prefix) : -1]
            if original in fixes:
                body = original
                speaker = None
            if body in fixes:
                words, english = fixes[body]
                if speaker:
                    words = [speaker, "说", "：", "“", *words, "”"]
                    english = ("She" if speaker == "她" else "I") + " says, “" + english + "”"
                if set(entry["target"]).intersection("".join(words)):
                    entry["tokens"] = [token(word) for word in words]
                    entry["english"] = english
                    entry["source"]["editorialCorrection"] = original
                    corrected += 1
            parts = []
            for part in entry["tokens"]:
                word = part["text"]
                # These corpus merges are grammar, not the dictionary's other
                # readings (穿着 chuānzhuó = clothing, 都会 dūhuì = metropolis).
                split = {"穿着": ["穿", "着"], "都会": ["都", "会"], "没收": ["没", "收"]}
                grammar_reading = (
                    "zhe" in part["pinyin"]
                    if word == "穿着"
                    else part["pinyin"].startswith("dōu")
                    if word == "都会"
                    else part["pinyin"].startswith("méi")
                    if word == "没收"
                    else False
                )
                if grammar_reading:
                    pieces = [token(w) for w in split[word]]
                    if word == "穿着":
                        pieces[-1]["pinyin"] = "zhe"
                    parts.extend(pieces)
                else:
                    if word in readings:
                        part["pinyin"] = readings[word]
                    if word in meanings:
                        part["meaning"] = meanings[word]
                    parts.append(part)
            sentence = "".join(part["text"] for part in parts)
            for position, part in enumerate(parts):
                if (sentence, part["text"]) in reading_fixes:
                    part["pinyin"], part["meaning"] = reading_fixes[(sentence, part["text"])]
                    entry["source"]["readingCorrection"] = "mandarin-reading-corrections.tsv"
                options = alignment.get(part["text"], []) + [
                    g.strip().removeprefix("to ") for g in part["meaning"].split(";")
                ]
                part["englishRanges"] = []
                occurrence = sum(p["text"] == part["text"] for p in parts[:position])
                for meaning in options:
                    if not meaning or len(meaning) > 55:
                        continue
                    matches = list(
                        re.finditer(
                            r"(?<!\w)" + re.escape(meaning) + r"(?!\w)",
                            entry["english"],
                            re.IGNORECASE,
                        )
                    )
                    if occurrence < len(matches):
                        hit = matches[occurrence]
                        part["englishRanges"] = [[hit.start(), hit.end()]]
                        break
                if part["pinyin"]:
                    assert part["meaning"] and not re.search("[\u3400-\u9fff]", part["meaning"]), (
                        entry["target"],
                        part,
                    )
            entry["tokens"] = parts
            entry["pinyin"] = " ".join(t["pinyin"] or t["text"] for t in parts)
        (DATA / filename).write_text(
            json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    # Rebuild aliases in introduction order after boundary corrections. An
    # advanced alternative must never displace a word's introduction example.
    known = set()
    words = defaultdict(list)
    for lesson in lessons:
        known.update(lesson.get("language_data", {}).get("characters", lesson["text"]))
        file = index["characters"][lesson["text"]]
        for entry in json.loads((DATA / file).read_text()):
            for part in entry["tokens"]:
                word = part["text"]
                if (
                    part["pinyin"]
                    and set(word).issubset(known)
                    and file not in words[word]
                    and len(words[word]) < 3
                ):
                    words[word].append(file)
    index["words"] = words
    (DATA / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Applied {corrected} explicit sentence corrections; aligned and validated all sentence token metadata."
    )


if __name__ == "__main__":
    main()
