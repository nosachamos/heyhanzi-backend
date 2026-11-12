"""Build bounded, on-demand Mandarin example files from pinned sentence/dictionary inputs.

This is an offline editorial tool, never a request-time text generator. It retains
source IDs, translations and contextual readings, rejects unavailable characters,
adds only useful dictionary-backed lexical prerequisites, and writes an audit.
See docs/learning-content.md for provenance and the remaining language review.
"""

import argparse
import gzip
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from build_hsk_seed import tone_marks

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REVISION = "02d75a8974683460ffcfa4e0d2673bc8113ceb67"
SENTENCE_SHA256 = "32e7ace25a6b66aade35391233d0f5ba5838d02662892e02e995031a0e42f776"
WORD_RE = re.compile(r"[\u3400-\u9fff]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sentences", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument(
        "--examples-only", action="store_true", help="Do not write any vocabulary migration data."
    )
    parser.add_argument(
        "--vocabulary-output",
        type=Path,
        default=ROOT / "alembic/data/0016_practical_vocabulary.json",
    )
    args = parser.parse_args()
    assert hashlib.sha256(args.sentences.read_bytes()).hexdigest() == SENTENCE_SHA256
    sources = json.loads((ROOT / "alembic/data/0012_sources/sources.json").read_text())
    assert (
        hashlib.sha256(args.dictionary.read_bytes()).hexdigest() == sources["sources"][-1]["sha256"]
    )
    base = json.loads((ROOT / "alembic/data/0012_curriculum.json").read_text())
    warm = json.loads((ROOT / "alembic/data/0004_warmup.json").read_text())["words"]
    lessons = [
        dict(
            text=w["text"],
            level=0,
            pronunciation=w["pronunciation"],
            characters=w["language_data"]["characters"],
        )
        for w in warm
    ] + base["characters"]
    all_characters = {ch for lesson in lessons for ch in lesson.get("characters", lesson["text"])}
    from app.services.learning_glosses import learner_translations
    from app.services.vocabulary_corrections import apply_vocabulary_corrections

    lexical = {}
    # Reconstruct the previous published catalogue from immutable migration
    # inputs; the frontend's current download must never become a build input.
    for item in sorted(apply_vocabulary_corrections(base["vocabulary"]), key=lambda w: (-w["rank"], w["id"])):
        if item["id"] == "cmn-6fcef30bb02f8043e1a7d56b":
            item["pronunciation"]["value"] = "lǎo po"
        key = (
            item["text"],
            item["pronunciation"]["value"]
            .lower()
            .replace(" ", "")
            .replace("'", "")
            .replace("’", ""),
        )
        item["translations"] = learner_translations(
            item["translations"], text=item["text"], pronunciation=item["pronunciation"]
        )
        lexical.setdefault(key, item)
    for file in ("0014_common_vocabulary.json", "0015_expanded_vocabulary.json"):
        for item in json.loads((ROOT / "alembic/data" / file).read_text()):
            lexical.setdefault(
                (item["text"], item["pronunciation"]["value"].replace(" ", "")), item
            )
    vocabulary = list(lexical.values())
    existing = {(w["text"], w["pronunciation"]["value"].replace(" ", "")) for w in vocabulary}
    byword = defaultdict(list)
    for w in vocabulary:
        byword[w["text"]].append(w)
    dictionary = defaultdict(list)
    for line in gzip.open(args.dictionary, "rt", encoding="utf-8"):
        match = re.match(r"\S+ (\S+) \[([^]]+)\] /(.+)/", line)
        if not match:
            continue
        text, reading, definitions = match.groups()
        if reading[0].isupper():
            continue
        glosses = [
            g
            for g in definitions.split("/")
            if not g.startswith(("CL:", "variant of", "see ", "old variant", "surname "))
        ]
        if glosses:
            dictionary[text].append((tone_marks(reading), glosses))
    overrides = {
        (w["text"], w["pinyin"]): w["meaning"]
        for w in json.loads((ROOT / "app/data/learning/mandarin-meanings.json").read_text())
    }
    editorial = defaultdict(list)
    for line in (Path(__file__).parent / "mandarin-editorial.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        target, tokens, english = line.split("\t")
        editorial[target].append(
            {"words": tokens.split(), "english": english, "origin": "HeyHanzi editorial"}
        )
    practical = {
        "到家": "to arrive home; exceptionally good",
        "好事": "good thing; good deed",
        "只见": "to see; to notice",
        "家里": "at home",
        "知觉": "consciousness; sensation",
        "不知": "not to know",
        "谁知道": "who knows",
        "一个": "one; a",
        "在家": "to be at home",
    }
    for line in (
        (Path(__file__).parent / "mandarin-practical-vocabulary.tsv").read_text().splitlines()
    ):
        if line and not line.startswith("#"):
            word, meaning = line.split("\t")
            practical[word] = meaning
    corpus = defaultdict(list)
    frequencies = Counter()
    for line in args.sentences.read_text().splitlines():
        sentence = json.loads(line)
        # Avoid upstream's much weaker single-character-headword pool. Our target
        # character may be inside an attested compound; no proper-name readings.
        if not any(len(w["simplified"]) > 1 for w in sentence["headwords"]):
            continue
        if re.search(r"[A-Za-z0-9]", sentence["hanzi"]):
            continue
        syllables = (sentence["pinyinNumbered"] or "").split()
        if len(WORD_RE.findall(sentence["hanzi"])) != len(syllables):
            continue
        tokens = []
        cursor = 0
        sound = 0
        forms = sorted(sentence["words"], key=lambda w: (-len(w), w))
        while cursor < len(sentence["hanzi"]):
            word = next(
                (w for w in forms if sentence["hanzi"].startswith(w, cursor)),
                sentence["hanzi"][cursor],
            )
            length = len(WORD_RE.findall(word))
            tokens.append(
                {
                    "text": word,
                    "pinyin": tone_marks(" ".join(syllables[sound : sound + length]))
                    if length
                    else "",
                }
            )
            cursor += len(word)
            sound += length
        if any(
            WORD_RE.search(t["text"]) and t["text"] not in dictionary and t["text"] not in byword
            for t in tokens
        ):
            continue
        item = {
            "words": [t["text"] for t in tokens],
            "readings": [t["pinyin"] for t in tokens],
            "english": sentence["english"],
            "origin": "Zhongdex",
            "source": {
                "dataset": "Zhongdex",
                "revision": REVISION,
                "id": sentence["id"],
                "license": "CC-BY-SA-4.0",
                "upstream": sentence["source"],
            },
        }
        frequencies.update(set(item["words"]))
        for char in set(WORD_RE.findall(sentence["hanzi"])):
            corpus[char].append(item)
    supplements = {}
    index = {}
    word_index = defaultdict(list)
    totals = Counter()
    review = []
    destination = ROOT / "app/data/learning/examples"
    destination.mkdir(parents=True, exist_ok=True)
    rendered = {}
    known = set()
    core = {}
    core_meanings = {}
    emitted_words = set()

    def reading(word, preferred=None):
        candidates = dictionary.get(word, [])
        if preferred:
            matching = next(
                (
                    item
                    for item in candidates
                    if item[0].replace(" ", "") == preferred.replace(" ", "")
                ),
                None,
            )
            if matching:
                return matching

            # Preserve contextual corpus readings instead of substituting a word's
            # other dictionary reading. Meaning falls back to our lesson/lexicon.
            def plain(value):
                return "".join(
                    c
                    for c in unicodedata.normalize("NFD", value.lower())
                    if not unicodedata.combining(c) and c != " "
                )

            same_sound = next(
                (item for item in candidates if plain(item[0]) == plain(preferred)), None
            )
            if same_sound:
                return (preferred if word in ("一", "不") else same_sound[0]), same_sound[1]
            lexical = byword.get(word, [])
            if lexical:
                lexical.sort(
                    key=lambda w: (
                        plain(w["pronunciation"]["value"]) != plain(preferred),
                        -w["rank"],
                    )
                )
                return preferred, [lexical[0]["translations"]["en"]]
            return preferred, [word]
        if word in core:
            return core[word]["value"], [
                overrides.get(
                    (word, core[word]["value"]),
                    next(
                        (
                            w["translations"]["en"]
                            for w in byword.get(word, [])
                            if w["pronunciation"]["value"] == core[word]["value"]
                        ),
                        core_meanings[word],
                    ),
                )
            ]
        ranked = sorted(byword.get(word, []), key=lambda w: -w["rank"])
        if ranked:
            return ranked[0]["pronunciation"]["value"], [ranked[0]["translations"]["en"]]
        return candidates[0] if candidates else ("", [word])

    def concise(gloss):
        gloss = re.sub(r"\[[^]]+\]|[\u3400-\u9fff]+(?:\|[\u3400-\u9fff]+)?", "", gloss)
        gloss = re.sub(
            r"\((?:bound form|literary|figuratively|coll[^)]*|classifier:[^)]*)\)", "", gloss
        )
        gloss = re.sub(r"\bsb\b", "somebody", gloss)
        gloss = re.sub(r"\bsth\b", "something", gloss)
        return re.sub(r"\s+", " ", gloss.split(";")[0]).strip(" ;,")[:110]

    def annotated(item, target):
        words = item["words"]
        english = item["english"]
        result = []
        for idx, word in enumerate(words):
            is_word = bool(WORD_RE.search(word))
            sound, glosses = (
                reading(word, item.get("readings", [None] * len(words))[idx])
                if is_word
                else ("", [])
            )
            glosses = [
                overrides.get((word, sound), practical.get(word, concise(g))) for g in glosses
            ]
            meaning = next((g for g in glosses if g), word) if is_word else ""
            # Exact lexical matches only. Idioms/implicit grammar never fabricate
            # alignments; the tooltip shows a parenthetical meaning instead.
            matches = []
            options = (
                ["I", "me", "my"]
                if word == "我"
                else ["you", "your"]
                if word == "你"
                else ["he", "him", "his"]
                if word == "他"
                else ["she", "her"]
                if word == "她"
                else []
            )
            for g in options + glosses:
                key = g.removeprefix("to ")
                if not key or key == word or len(key) > 45:
                    continue
                hits = list(
                    re.finditer(r"(?<!\w)" + re.escape(key) + r"(?!\w)", english, re.IGNORECASE)
                )
                occurrence = sum(previous == word for previous in words[:idx])
                if occurrence < len(hits):
                    hits = hits[occurrence : occurrence + 1]
                    matches = [[hit.start(), hit.end()] for hit in hits]
                    meaning = g
                    break
            result.append(
                {"text": word, "pinyin": sound, "meaning": meaning, "englishRanges": matches}
            )
            if is_word:
                emitted_words.add(word)
                if word not in core and word not in byword and word in dictionary:
                    canonical_sound = reading(word)[0]
                    key = word + "|" + canonical_sound
                    supplements[key] = {
                        "id": "cmn-" + hashlib.sha256(key.encode()).hexdigest()[:24],
                        "text": word,
                        "translations": {"en": practical.get(word, meaning), "zh": word},
                        "pronunciation": {"scheme": "pinyin", "value": canonical_sound},
                        "characters": list(dict.fromkeys(word)),
                        "rank": 10**7 + frequencies[word],
                        "level": None,
                    }
        return {
            "target": target,
            "tokens": result,
            "english": english,
            "pinyin": " ".join(t["pinyin"] or t["text"] for t in result),
            "source": item.get("source", {"dataset": item["origin"], "license": "CC-BY-SA-4.0"}),
        }

    for lesson in lessons:
        target = lesson["text"]
        known.update(lesson.get("characters", target))
        core[target] = lesson["pronunciation"]
        core_meanings[target] = lesson.get("translations", {}).get(
            "en", next((w["translations"]["en"] for w in warm if w["text"] == target), target)
        )

        def eligible(item):
            return all(
                set(WORD_RE.findall(word)).issubset(known)
                and (
                    not WORD_RE.search(word) or word in dictionary or word in core or word in byword
                )
                for word in item["words"]
            )

        options = [e for e in editorial[target] if eligible(e)]
        options += sorted(
            (e for e in corpus[target] if eligible(e)),
            key=lambda e: (len(e["words"]), len("".join(e["words"])), e["english"]),
        )
        # If a character has no corpus example at its introduction point, try a
        # small set of semantically appropriate editorial frames for common nouns.
        if not options:
            recipes = {
                "牛": ("牛", "cow"),
                "鱼": ("鱼", "fish"),
                "肉": ("肉", "meat"),
                "鸟": ("鸟", "bird"),
                "羊": ("羊", "sheep"),
                "猪": ("猪", "pig"),
                "蛇": ("蛇", "snake"),
                "狼": ("狼", "wolf"),
                "熊": ("熊", "bear"),
                "鸡": ("鸡", "chicken"),
                "兔": ("兔子", "rabbit"),
                "猴": ("猴子", "monkey"),
                "龟": ("乌龟", "tortoise"),
                "奶": ("奶奶", "grandmother"),
                "妻": ("妻子", "wife"),
                "婆": ("老婆", "wife"),
                "姨": ("姨妈", "aunt"),
                "姑": ("姑姑", "aunt"),
                "叔": ("叔叔", "uncle"),
                "侄": ("侄子", "nephew"),
                "甥": ("外甥", "nephew"),
                "纸": ("纸", "paper"),
                "刀": ("刀", "knife"),
                "帽": ("帽子", "hat"),
                "椅": ("椅子", "chair"),
                "筷": ("筷子", "chopsticks"),
                "裙": ("裙子", "skirt"),
                "裤": ("裤子", "trousers"),
                "铅": ("铅笔", "pencil"),
                "勺": ("勺子", "spoon"),
                "膏": ("牙膏", "toothpaste"),
                "桶": ("桶", "bucket"),
                "砖": ("砖", "brick"),
                "糖": ("糖", "sugar"),
                "米": ("米饭", "rice"),
                "蛋": ("蛋", "egg"),
                "汤": ("汤", "soup"),
                "盐": ("盐", "salt"),
                "醋": ("醋", "vinegar"),
                "糕": ("蛋糕", "cake"),
                "啤": ("啤酒", "beer"),
                "蕉": ("香蕉", "banana"),
                "萄": ("葡萄", "grape"),
                "橙": ("橙子", "orange"),
                "咖": ("咖啡", "coffee"),
                "剑": ("剑", "sword"),
                "江": ("江", "river"),
                "宫": ("宫殿", "palace"),
                "田": ("农田", "farmland"),
            }
            recipe = recipes.get(target)
            if recipe:
                word, noun = recipe
                plural = {
                    "cow": "cows",
                    "bird": "birds",
                    "pig": "pigs",
                    "snake": "snakes",
                    "wolf": "wolves",
                    "bear": "bears",
                    "rabbit": "rabbits",
                    "monkey": "monkeys",
                    "tortoise": "tortoises",
                    "knife": "knives",
                    "hat": "hats",
                    "chair": "chairs",
                    "skirt": "skirts",
                    "pencil": "pencils",
                    "spoon": "spoons",
                    "bucket": "buckets",
                    "brick": "bricks",
                    "egg": "eggs",
                    "banana": "bananas",
                    "grape": "grapes",
                    "orange": "oranges",
                    "sword": "swords",
                    "river": "rivers",
                    "palace": "palaces",
                }
                noun = plural.get(noun, noun)
                frames = [
                    (["我", "喜欢", word, "。"], f"I like {noun}."),
                    (["你", "喜欢", word, "吗", "？"], f"Do you like {noun}?"),
                    (["他", "也", "喜欢", word, "。"], f"He likes {noun} too."),
                ]
                if target in "奶妻婆姨姑叔侄甥":
                    frames = [
                        (["我", "的", word, "在", "家", "。"], f"My {noun} is at home."),
                        (["我", "想", word, "了", "。"], f"I miss my {noun}."),
                        (["我", "的", word, "很", "好", "。"], f"My {noun} is very nice."),
                    ]
                options.extend(
                    {"words": tokens, "english": english, "origin": "HeyHanzi editorial"}
                    for tokens, english in frames
                    if eligible({"words": tokens})
                )
        unique = []
        texts = set()
        for item in options:
            text = "".join(item["words"])
            if text not in texts:
                unique.append(item)
                texts.add(text)
            if len(unique) == 3:
                break
        if unique and len(unique) < 3 and "说" in known:
            # A short dialogue supplies a different speech context without adding
            # unknown words or changing the original sentence's grammar/reading.
            body = unique[0]
            for speaker, english_speaker in [("她", "She"), ("我", "I")]:
                words = [speaker, "说", "：", "“", *body["words"], "”"]
                variant = {
                    "words": words,
                    "english": f"{english_speaker} says, “{body['english']}”",
                    "origin": "HeyHanzi adaptation",
                    "source": {
                        **body.get(
                            "source", {"dataset": body["origin"], "license": "CC-BY-SA-4.0"}
                        ),
                        "adaptation": "reported-speech dialogue",
                    },
                }
                if "readings" in body:
                    variant["readings"] = [
                        reading(speaker)[0],
                        "shuō",
                        "",
                        "",
                        *body["readings"],
                        "",
                    ]
                if eligible(variant) and len(unique) < 3:
                    unique.append(variant)
        if len(unique) < 3:
            # Bound forms may lack an introduced usable compound. Record the
            # coverage honestly; quoted-word practice is not a contextual example.
            review.append(
                {
                    "target": target,
                    "level": lesson["level"],
                    "reason": "bound form or missing usable context at introduction",
                    "count": len(unique),
                }
            )
        advanced = [
            item
            for item in editorial[target]
            if not eligible(item)
            and all(
                set(WORD_RE.findall(word)).issubset(all_characters)
                and (not WORD_RE.search(word) or word in dictionary or word in byword)
                for word in item["words"]
            )
        ][:3]
        annotated_examples = [annotated(item, target) for item in advanced + unique]
        file = "-".join(f"{ord(char):x}" for char in target) + ".json"
        rendered[file] = (
            json.dumps(annotated_examples, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        index[target] = file
        for word in {t["text"] for e in annotated_examples for t in e["tokens"] if t["pinyin"]}:
            if set(WORD_RE.findall(word)).issubset(known) and len(word_index[word]) < 3:
                word_index[word].append(file)
        for item in unique:
            totals[(lesson["level"], item["origin"])] += 1
    # Add a modest contemporary practical supplement, filtered to the programme's
    # characters and verified dictionary entries; no invented HSK assignments.
    unverified = []
    for word, meaning in practical.items():
        phrases = {
            "手机号码": "shǒu jī hào mǎ",
            "电话号码": "diàn huà hào mǎ",
            "手机号": "shǒu jī hào",
            "视频通话": "shì pín tōng huà",
            "换货": "huàn huò",
            "垃圾袋": "lā jī dài",
            "水电费": "shuǐ diàn fèi",
            "牙疼": "yá téng",
            "斑马线": "bān mǎ xiàn",
        }
        if (word not in dictionary and word not in phrases) or not set(word).issubset(
            all_characters
        ):
            unverified.append(word)
            continue
        sound = phrases[word] if word in phrases else dictionary[word][0][0]
        key = word + "|" + sound
        if (word, sound.replace(" ", "")) in existing:
            continue
        supplements[key] = {
            "id": "cmn-" + hashlib.sha256(key.encode()).hexdigest()[:24],
            "text": word,
            "translations": {"en": meaning, "zh": word},
            "pronunciation": {"scheme": "pinyin", "value": sound},
            "characters": list(dict.fromkeys(word)),
            "rank": 10**8,
            "level": None,
        }
    seed = json.dumps(list(supplements.values()), ensure_ascii=False, indent=2) + "\n"
    if not args.examples_only:
        if args.vocabulary_output.exists() and args.vocabulary_output.read_text() != seed:
            raise RuntimeError(
                "Existing migration data is immutable. Use --examples-only or a new --vocabulary-output for a forward migration."
            )
        args.vocabulary_output.write_text(seed)
    for file, content in rendered.items():
        (destination / file).write_text(content)
    (destination / "index.json").write_text(
        json.dumps({"characters": index, "words": word_index}, ensure_ascii=False, indent=2) + "\n"
    )
    audit = {
        "source": {
            "url": f"https://github.com/saymei/zhongdex/blob/{REVISION}/data/sentences.jsonl",
            "revision": REVISION,
            "sha256": SENTENCE_SHA256,
            "license": "CC-BY-SA-4.0",
        },
        "lessons": len(index),
        "examples": sum(len(json.loads(content)) for content in rendered.values()),
        "introductionEligibleExamples": sum(totals.values()),
        "supplementaryVocabulary": len(supplements),
        "coverage": {
            str(level): {origin: count for (lev, origin), count in totals.items() if lev == level}
            for level in range(7)
        },
        "introductionCoverageGaps": review,
        "practicalWordsNotImported": unverified,
        "reviewStatus": "automated prerequisite/alignment validation; native-speaker proofreading still required",
    }
    (ROOT / "app/data/learning/examples-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in audit.items() if k not in ("source", "introductionCoverageGaps")},
            ensure_ascii=False,
            indent=2,
        )
    )
    print("Character-practice introductions:", len(review))


if __name__ == "__main__":
    main()
