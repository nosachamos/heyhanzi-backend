"""Build exact-identity common-word examples from pinned real sentences/editorial TSVs.

No runtime text generation. Candidate ranking is not a claim of complete semantic
review: source, selected target, lexical controls and review flags remain visible.
"""

import argparse
import collections
import functools
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

from build_hsk_seed import tone_marks

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT.parent / "heyhanzi-ui-web"
SHA = "32e7ace25a6b66aade35391233d0f5ba5838d02662892e02e995031a0e42f776"
REV = "02d75a8974683460ffcfa4e0d2673bc8113ceb67"
HAN = re.compile(r"[\u3400-\u9fff]")


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def key(p):
    return re.sub(r"[\s'’\-]", "", unicodedata.normalize("NFC", p.lower()))


def forms(p):
    return [x.strip() for x in re.split(r"[/／]", p) if x.strip()]


def speech_reading(v, p):
    return next(
        (
            f
            for f in forms(v["pronunciation"]["value"])
            if lexical_key(v["text"], f) == lexical_key(v["text"], p)
        ),
        p,
    )


def lexical_key(text, p):
    # The only tone aliases here are productive 一/不 sandhi, never arbitrary
    # homograph tones or neutral/full-tone changes.
    value = key(p)
    if text == "个" and value == "ge":
        value = "gè"
    if text.startswith("一"):
        value = re.sub(r"^y[íì]", "yī", value)
    if text.startswith("不"):
        value = re.sub(r"^bú", "bù", value)
    return value


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sentences", type=Path, default=Path("/tmp/heyhanzi-content/zhongdex-sentences.jsonl")
    )
    ap.add_argument("--output", type=Path, default=ROOT / "app/data/learning/common-word-examples")
    ap.add_argument(
        "--report",
        type=Path,
        default=UI / "artifacts/learning/natural-common-word-examples/compiler-report.json",
    )
    ap.add_argument("--require-complete", action="store_true")
    args = ap.parse_args()
    assert hashlib.sha256(args.sentences.read_bytes()).hexdigest() == SHA
    public = read(UI / "public/curriculum-data/mandarin-hsk2025.json")
    targets = {v["id"]: v for v in public["vocabulary"]}
    for f in [
        "0020_story_vocabulary.json",
        "0022_notebook_grammar_vocabulary.json",
        "0024_common_word_example_support.json",
    ]:
        for v in read(ROOT / "alembic/data" / f):
            targets[v["id"]] = v
    corrections = ROOT / "app/data/learning/vocabulary-corrections.json"
    if corrections.exists():
        sys.path.insert(0, str(ROOT))
        from app.services.vocabulary_corrections import apply_vocabulary_corrections

        corrected = apply_vocabulary_corrections(list(targets.values()))
        if corrected is not None:
            targets = {v["id"]: v for v in corrected}
    sys.path.insert(0, str(ROOT))
    from app.services.learning_glosses import (
        learner_translations,
        primary_meaning_corrections,
        primary_translations,
        word_meaning_corrections,
        word_translations,
    )

    primary_corrections = []
    for value in targets.values():
        if not value.get("sense_key"):
            correction = word_meaning_corrections().get(value["id"])
            original_display = learner_translations(
                value["translations"], text=value["text"], pronunciation=value["pronunciation"]
            )
            if correction:
                if original_display["en"] != correction["old"]:
                    raise ValueError(
                        "Reviewed default meaning no longer matches its source: " + value["id"]
                    )
                primary_corrections.append(
                    {
                        "text": value["text"],
                        "reading_key": key(value["pronunciation"]["value"]),
                        "old": correction["old"],
                        "new": correction["new"],
                        "vocabularyId": value["id"],
                    }
                )
            value["translations"] = word_translations(
                value["translations"],
                vocabulary_id=value["id"],
                text=value["text"],
                pronunciation=value["pronunciation"],
                sense_key=value.get("sense_key", ""),
            )
    write(
        ROOT / "app/data/learning/common-word-primary-meaning-corrections.json", primary_corrections
    )
    primary_meaning_corrections.cache_clear()
    warm = read(ROOT / "alembic/data/0004_warmup.json")["words"]
    core = {v["text"]: v for v in [*warm, *public["characters"]]}
    supported = {c for text in core for c in text}
    stages = {c: i for i, text in reversed(list(enumerate(core))) for c in text}
    all_lex = collections.defaultdict(list)
    for v in targets.values():
        for form in forms(v["pronunciation"]["value"]):
            all_lex[v["text"], lexical_key(v["text"], form)].append(v)
    for text, v in core.items():
        item = {
            **v,
            "id": None,
            "sense_key": "",
            "translations": primary_translations(
                v.get("translations", {"en": text}), text=text, pronunciation=v["pronunciation"]
            ),
        }
        all_lex[text, lexical_key(text, v["pronunciation"]["value"])].append(item)
    trie = {}
    for text, _ in all_lex:
        node = trie
        for c in text:
            node = node.setdefault(c, {})
        node["_word"] = text
    target_keys = collections.defaultdict(list)
    for v in targets.values():
        for form in forms(v["pronunciation"]["value"]):
            target_keys[v["text"], lexical_key(v["text"], form)].append(v)

    def choose(word, pin, target=None):
        options = all_lex.get((word, lexical_key(word, pin)), [])
        if target:
            return (
                target
                if any(
                    lexical_key(word, pin) == lexical_key(word, f)
                    for f in forms(target["pronunciation"]["value"])
                )
                else None
            )
        # Do not silently use a distinct course sense as generic prerequisite.
        options = [x for x in options if not x.get("sense_key")]
        if not options:
            return None
        return min(
            options,
            key=lambda x: (
                x["id"] is not None if len(word) == 1 else x["id"] is None,
                x.get("rank", 10**20),
            ),
        )

    def token(word, pin, item):
        out = {
            "text": word,
            "pinyin": pin,
            "meaning": item["translations"]["en"],
            "englishRanges": [],
        }
        if item.get("id"):
            out["vocabularyId"] = item["id"]
        return out

    def tokenize(hanzi, syllables, target, start, end, preferred=()):
        offsets = [0]
        for c in hanzi:
            offsets.append(offsets[-1] + bool(HAN.fullmatch(c)))
        allowed_edges = {0, len(hanzi)}
        cursor = 0
        source_forms = sorted(preferred, key=lambda w: (-len(w), w))
        while cursor < len(hanzi):
            source_word = next(
                (w for w in source_forms if hanzi.startswith(w, cursor)), hanzi[cursor]
            )
            cursor += len(source_word)
            allowed_edges.add(cursor)
        if start not in allowed_edges or end not in allowed_edges:
            return None

        @functools.lru_cache(None)
        def walk(pos):
            if pos == len(hanzi):
                return (0, [])
            if not HAN.fullmatch(hanzi[pos]):
                tail = walk(pos + 1)
                return (
                    None
                    if tail is None
                    else (
                        tail[0],
                        [
                            {"text": hanzi[pos], "pinyin": "", "meaning": "", "englishRanges": []},
                            *tail[1],
                        ],
                    )
                )
            options = []
            node = trie
            for stop in range(pos, len(hanzi)):
                node = node.get(hanzi[stop])
                if node is None:
                    break
                if "_word" not in node:
                    continue
                edge = stop + 1
                if edge not in allowed_edges:
                    continue
                if pos < start < edge or pos < end < edge or start < pos < end:
                    continue
                if pos == start and edge != end:
                    continue
                word = hanzi[pos:edge]
                pin = " ".join(syllables[offsets[pos] : offsets[edge]])
                item = choose(word, pin, target if pos == start else None)
                if item is None:
                    continue
                tail = walk(edge)
                if tail is None:
                    continue
                # Prefer actual corpus word boundaries without inheriting upstream
                # segmentation errors, then fewer declared lexical dependencies.
                cost = tail[0] + 1 + (0 if word in preferred else 0.15)
                out = token(word, pin, item)
                if pos == start:
                    out["pinyin"] = speech_reading(target, pin)
                    out["vocabularyId"] = target["id"]
                options.append((cost, [out, *tail[1]]))
            return min(options, key=lambda x: x[0]) if options else None

        result = walk(0)
        return result[1] if result else None

    pool = collections.defaultdict(dict)
    stats = collections.Counter()
    stopwords = {
        "a",
        "an",
        "the",
        "to",
        "of",
        "or",
        "and",
        "in",
        "on",
        "for",
        "with",
        "as",
        "at",
        "by",
        "be",
        "sth",
        "sb",
        "one",
        "used",
        "form",
        "bound",
        "used",
        "classifier",
        "particle",
        "also",
        "pr",
        "taiwan",
        "literary",
        "variant",
        "see",
        "surname",
    }

    def terms(s):
        return {w for w in re.findall("[a-z]+", s.lower()) if len(w) > 2 and w not in stopwords}

    def add(
        v, hanzi, pin, tokens, english, source, headword=False, editorial=False, target_span=None
    ):
        if any(c not in supported for c in HAN.findall(hanzi)):
            stats["unsupportedCharacters"] += 1
            return
        if re.search(r'我在学[“「"].+[”」"]', hanzi):
            return
        overlap = terms(v["translations"]["en"]) & terms(english)
        sense = bool(v.get("sense_key"))
        if sense and not editorial:
            return
        stage = max((stages[c] for c in HAN.findall(hanzi)), default=0)
        ownstage = max((stages.get(c, 10**6) for c in v["text"]), default=0)
        # Existing translations and original source records remain auditable.
        entry = {
            "target": v["text"],
            "targetVocabularyId": v["id"],
            "tokens": tokens,
            "pinyin": " ".join(t["pinyin"] for t in tokens if t["pinyin"]),
            "english": english,
            "source": source,
        }
        if target_span:
            entry["targetSpan"] = target_span
        flags = []
        if not editorial and not overlap:
            flags.append("meaning-needs-context-review")
        if stage > ownstage:
            flags.append("later-character-dependency")
        score = (
            0 if editorial else 1,
            0 if stage <= ownstage else 1,
            0 if headword else 1,
            0 if overlap else 1,
            max(0, stage - ownstage),
            len(HAN.findall(hanzi)),
            len(tokens),
        )
        candidate = {
            "entry": entry,
            "score": score,
            "reviewFlags": flags,
            "meaningOverlap": sorted(overlap),
            "characterStage": stage,
            "targetCharacterStage": ownstage,
        }
        previous = pool[v["id"]].get(hanzi)
        if previous is None or score < previous["score"]:
            pool[v["id"]][hanzi] = candidate

    upstream_boundaries = {}
    # Full-word span / full syllable alignment fixes upstream segmented 数+学 etc.
    for line in args.sentences.open():
        s = json.loads(line)
        stats["sourceSentences"] += 1
        raw = (s.get("pinyinNumbered") or "").split()
        hanzi = s["hanzi"]
        cursor = 0
        edges = {0}
        source_forms = sorted(s["words"], key=lambda word: (-len(word), word))
        while cursor < len(hanzi):
            word = next(
                (word for word in source_forms if hanzi.startswith(word, cursor)), hanzi[cursor]
            )
            cursor += len(word)
            edges.add(cursor)
        upstream_boundaries[s["id"]] = (hanzi, edges)
        if (
            len(HAN.findall(hanzi)) != len(raw)
            or re.search("[A-Za-z0-9]", hanzi)
            or not any(len(h["simplified"]) > 1 for h in s["headwords"])
        ):
            continue
        if any(c not in supported for c in HAN.findall(hanzi)):
            continue
        if re.search(r'我在学[“「"].+[”」"]', hanzi):
            continue
        sounds = [tone_marks(x) for x in raw]
        offsets = [0]
        for c in hanzi:
            offsets.append(offsets[-1] + bool(HAN.fullmatch(c)))
        for start in range(len(hanzi)):
            node = trie
            for stop in range(start, len(hanzi)):
                node = node.get(hanzi[stop])
                if node is None:
                    break
                if "_word" not in node:
                    continue
                word = hanzi[start : stop + 1]
                pin = " ".join(sounds[offsets[start] : offsets[stop + 1]])
                for v in target_keys.get((word, lexical_key(word, pin)), []):
                    if v.get("sense_key"):
                        continue
                    tokens = tokenize(hanzi, sounds, v, start, stop + 1, s["words"])
                    if tokens is None:
                        stats["unknownLexicalDependencies"] += 1
                        continue
                    source = {
                        "dataset": "Zhongdex",
                        "revision": REV,
                        "id": s["id"],
                        "license": "CC-BY-SA-4.0",
                        "upstream": s["source"],
                        "originalPinyin": s["pinyinNumbered"],
                        "targetMatch": "exact full-word span and lexical reading",
                    }
                    add(
                        v,
                        hanzi,
                        pin,
                        tokens,
                        s["english"],
                        source,
                        any(h["simplified"] == word for h in s["headwords"]),
                    )
    # Current annotated corpus includes editorial contexts absent from upstream.
    idx = read(ROOT / "app/data/learning/examples/index.json")
    for file in sorted(set(idx["characters"].values())):
        for e in read(ROOT / "app/data/learning/examples" / file):
            if (
                e.get("source", {}).get("dataset") == "HeyHanzi character practice"
                or e.get("source", {}).get("adaptation") == "reported-speech dialogue"
            ):
                continue
            hanzi = "".join(t["text"] for t in e["tokens"])
            cursor = 0
            for original in e["tokens"]:
                word = original["text"]
                start = cursor
                cursor += len(word)
                for v in target_keys.get((word, lexical_key(word, original["pinyin"])), []):
                    if v.get("sense_key"):
                        continue
                    original_boundaries = upstream_boundaries.get(e.get("source", {}).get("id"))
                    if (
                        original_boundaries
                        and original_boundaries[0] == hanzi
                        and (
                            start not in original_boundaries[1]
                            or cursor not in original_boundaries[1]
                        )
                    ):
                        stats["legacyCompoundBoundaryRejects"] += 1
                        continue
                    converted = []
                    for t in e["tokens"]:
                        if not HAN.search(t["text"]):
                            converted.append({**t})
                            continue
                        item = choose(t["text"], t["pinyin"], v if t is original else None)
                        if item is None:
                            break
                        converted.append(
                            token(
                                t["text"],
                                speech_reading(v, t["pinyin"]) if t is original else t["pinyin"],
                                item,
                            )
                        )
                    else:
                        add(
                            v,
                            hanzi,
                            e["pinyin"],
                            converted,
                            e["english"],
                            {**e["source"], "importedAnnotatedFile": file},
                            editorial=False,
                        )
    editorial_errors = []
    invalid_editorial_ids = set()
    overrides = [
        item
        for file in sorted(
            (ROOT / "scripts/curriculum").glob("common-word-editorial-token-senses*.json")
        )
        for item in read(file)
    ]
    span_proofs = {}
    for file in sorted((ROOT / "scripts/curriculum").glob("common-word-editorial-spans*.json")):
        for identity, proof in read(file).items():
            if identity in span_proofs and span_proofs[identity] != proof:
                raise ValueError("Conflicting bound-form proofs: " + identity)
            span_proofs[identity] = proof
    spans_used = set()
    override_used = set()
    editorial_files = [
        "common-word-editorial.tsv",
        "common-word-editorial-senses.tsv",
        "common-word-editorial-additional.tsv",
    ] + [
        file.name
        for file in sorted((ROOT / "scripts/curriculum").glob("common-word-editorial-review-*.tsv"))
    ]
    for name in editorial_files:
        file = ROOT / "scripts/curriculum" / name
        if not file.exists():
            continue
        for line_no, line in enumerate(file.read_text().splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            try:
                identity, segmented, pinyin, english = line.split("\t")
                v = targets[identity]
                words = segmented.split()
                sounds = pinyin.split()
                scoped = [
                    (n, o)
                    for n, o in enumerate(overrides)
                    if o["targetId"] == identity and o["sentence"] == "".join(words)
                ]

                @functools.lru_cache(None)
                def align(i, j):
                    if i == len(words):
                        return [] if j == len(sounds) else None
                    word = words[i]
                    if not HAN.search(word):
                        tail = align(i + 1, j)
                        return (
                            None
                            if tail is None
                            else [
                                {"text": word, "pinyin": "", "meaning": "", "englishRanges": []},
                                *tail,
                            ]
                        )
                    for n in range(1, len(HAN.findall(word)) + 1):
                        pin = " ".join(sounds[j : j + n])
                        is_target = word == v["text"] and any(
                            lexical_key(word, pin) == lexical_key(word, f)
                            for f in forms(v["pronunciation"]["value"])
                        )
                        explicit = [
                            targets[o["vocabularyId"]]
                            for _, o in scoped
                            if o["tokenText"] == word
                            and o["occurrence"] == sum(w == word for w in words[:i])
                        ]
                        item = choose(
                            word,
                            pin,
                            v if is_target else explicit[0] if len(explicit) == 1 else None,
                        )
                        if item is None:
                            continue
                        tail = align(i + 1, j + n)
                        if tail is not None:
                            return [
                                token(word, speech_reading(v, pin) if is_target else pin, item),
                                *tail,
                            ]
                    return None

                tokens = align(0, 0)
                if tokens is None:
                    raise ValueError("Unknown lexical dependency or reading/syllable alignment")
                target_span = None
                proof = span_proofs.get(identity)
                if proof and proof["sentence"] == "".join(words):
                    matches = [
                        (i, t) for i, t in enumerate(tokens) if t["text"] == proof["tokenText"]
                    ]
                    if len(matches) != 1:
                        raise ValueError("Bound-form proof must select one complete lexical token")
                    i, t = matches[0]
                    start, end = proof["targetStart"], proof["targetEnd"]
                    if (
                        not t.get("vocabularyId")
                        or not (0 <= start < end <= len(t["text"]))
                        or t["text"][start:end] != v["text"]
                    ):
                        raise ValueError("Bound-form target is not the declared whole-word subspan")
                    syllables = t["pinyin"].split()
                    if len(syllables) != len(HAN.findall(t["text"])):
                        raise ValueError(
                            "Bound-form proof needs unambiguous per-character syllable alignment"
                        )
                    recorded = " ".join(syllables[start:end])
                    if not choose(v["text"], proof["targetPinyin"], v) or lexical_key(
                        v["text"], recorded
                    ) != lexical_key(v["text"], proof["targetPinyin"]):
                        raise ValueError("Bound-form proof reading differs from word/context")
                    target_span = {
                        "tokenIndex": i,
                        "start": start,
                        "end": end,
                        "text": v["text"],
                        "pinyin": proof["targetPinyin"],
                        "vocabularyId": identity,
                        "meaning": proof["meaning"],
                    }
                    spans_used.add(identity)
                if not any(t.get("vocabularyId") == identity for t in tokens) and not target_span:
                    raise ValueError("Missing exact target token or explicit bound-form proof")
                for number, override in scoped:
                    matches = [t for t in tokens if t["text"] == override["tokenText"]]
                    if len(matches) <= override["occurrence"]:
                        raise ValueError("Contextual token override absent")
                    t = matches[override["occurrence"]]
                    lex = targets[override["vocabularyId"]]
                    if t["text"] != lex["text"] or not choose(t["text"], t["pinyin"], lex):
                        raise ValueError("Contextual token override reading mismatch")
                    t["vocabularyId"] = lex["id"]
                    t["meaning"] = lex["translations"]["en"]
                    override_used.add(number)
                add(
                    v,
                    "".join(words),
                    pinyin,
                    tokens,
                    english,
                    {
                        "dataset": "HeyHanzi editorial",
                        "license": "CC-BY-SA-4.0",
                        "file": name,
                        "line": line_no,
                        "review": "Explicit target meaning authored for this vocabulary identity",
                    },
                    editorial=True,
                    target_span=target_span,
                )
            except Exception as error:
                invalid_editorial_ids.add(line.split("\t")[0])
                editorial_errors.append(
                    {
                        "file": name,
                        "line": line_no,
                        "id": line.split("\t")[0],
                        "error": str(error),
                        "record": line,
                    }
                )
    args.output.mkdir(parents=True, exist_ok=True)
    index = {"version": 1, "words": {}}
    shards = collections.defaultdict(dict)
    missing = []
    review = []
    invalid_editorial_ids.update(
        override["targetId"] for n, override in enumerate(overrides) if n not in override_used
    )
    invalid_editorial_ids.update(span_proofs.keys() - spans_used)
    for identity, v in targets.items():
        candidates = (
            []
            if identity in invalid_editorial_ids
            else sorted(pool[identity].values(), key=lambda x: x["score"])
        )
        if not candidates:
            missing.append(v)
            continue
        selected = candidates[:1]
        filename = hashlib.sha256(identity.encode()).hexdigest()[:2] + ".json"
        index["words"][identity] = {
            "text": v["text"],
            "reading_key": key(v["pronunciation"]["value"]),
            "sense_key": v.get("sense_key", ""),
            "file": filename,
        }
        shards[filename][identity] = [x["entry"] for x in selected]
        review.append(
            {
                "id": identity,
                "text": v["text"],
                "pinyin": v["pronunciation"]["value"],
                "meaning": v["translations"]["en"],
                "sense_key": v.get("sense_key", ""),
                "selected": selected,
                "candidateCount": len(candidates),
            }
        )
    for file, data in shards.items():
        write(args.output / file, data)
    write(args.output / "index.json", index)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "targets": len(targets),
        "covered": len(index["words"]),
        "missing": len(missing),
        "explicitSenseTargets": sum(bool(v.get("sense_key")) for v in targets.values()),
        "sourceSha256": SHA,
        "primaryMeaningCorrections": len(primary_corrections),
        "invalidEditorialTargets": sorted(invalid_editorial_ids),
        "boundFormProofs": len(span_proofs),
        "unusedBoundFormProofs": sorted(span_proofs.keys() - spans_used),
        "stats": dict(stats),
        "editorialErrors": editorial_errors,
        "contextualOverrides": len(overrides),
        "unusedContextualOverrides": [o for n, o in enumerate(overrides) if n not in override_used],
        "reviewStatus": "Candidate context/reading/dependency checks; semantic shortlist remains auditable, not a claim of native proofreading.",
        "missingTargets": missing,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    args.report.with_name("selected-context-review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n"
    )
    args.report.with_name("missing-targets.json").write_text(
        json.dumps(missing, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ["missingTargets", "editorialErrors"]},
            ensure_ascii=False,
        )
    )
    print("editorialErrors", len(editorial_errors))
    if args.require_complete and (
        missing or editorial_errors or len(override_used) != len(overrides)
    ):
        raise SystemExit("Incomplete common-word examples; see report")


if __name__ == "__main__":
    main()
