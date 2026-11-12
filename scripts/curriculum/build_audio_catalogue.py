"""Freeze the allowlist for all authored sentences and existing word practices."""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from app.services.example_content_policy import reject_lexical_placeholders
from app.services.learning_examples import span_reading_matches, valid_common_target
from app.services.sentence_audio import DIRECTORY, VOICE_VERSION, sentence_id
from app.services.vocabulary_corrections import apply_vocabulary_corrections


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vocabulary", type=Path, required=True)
    parser.add_argument("--examples", type=Path, default=DIRECTORY.parent / "learning/examples")
    parser.add_argument("--common-examples", type=Path, default=DIRECTORY.parent / "learning/common-word-examples")
    parser.add_argument("--output", type=Path, default=DIRECTORY,
                        help="Write a staged catalogue here for review before replacing current inventory")
    args = parser.parse_args()
    root = args.examples
    index = json.loads((root / "index.json").read_text())
    vocabulary = json.loads(args.vocabulary.read_text())
    words = apply_vocabulary_corrections(vocabulary["vocabulary"])
    common_index = json.loads((args.common_examples / "index.json").read_text())["words"]
    missing = {word["id"] for word in words} - set(common_index)
    if missing:
        raise ValueError(f"{len(missing)} vocabulary identities lack authored common-word examples; finish content before audio inventory")
    readings = defaultdict(set)
    for item in words:
        for reading in item["pronunciation"]["value"].split("/"):
            readings[item["text"]].add("".join(reading.split()))

    def needs_control(text):
        return len(text) == 1 or len(readings[text]) > 1
    contextual = defaultdict(list)
    for line in (Path(__file__).parent / "audio-reading-controls.tsv").read_text().splitlines():
        if line and not line.startswith("#"):
            sentence, start, end, word, sound = line.split("\t")
            start, end = int(start), int(end)
            if sentence[start:end] != word:
                raise ValueError("Contextual reading does not match its sentence")
            contextual[sentence].append({"text": word, "pinyin": sound, "start": start, "end": end})
    shards = defaultdict(dict)
    ordered = []

    def add(text, pinyin, kind, **details):
        audio_id = sentence_id(text, pinyin)
        reject_lexical_placeholders({audio_id: {'text': text}})
        if audio_id not in shards[audio_id[:2]]:
            shards[audio_id[:2]][audio_id] = {"text": text, "pinyin": pinyin, "kind": kind, **details}
            ordered.append(audio_id)
        elif details.get('contextualReadings'):
            # Exact text/pinyin already binds pronunciation; retain the complete
            # authored token controls when a common-word example shares audio.
            shards[audio_id[:2]][audio_id]['contextualReadings'] = details['contextualReadings']

    for name in index["characters"].values():
        for entry in json.loads((root / name).read_text()):
            text = "".join(t["text"] for t in entry["tokens"])
            for control in contextual.get(text, []):
                offset = 0
                for token in entry["tokens"]:
                    end = offset + len(token["text"])
                    if offset == control["start"] and end == control["end"]:
                        if token["pinyin"] != control["pinyin"]:
                            raise ValueError("Contextual control differs from authored pinyin")
                        break
                    offset = end
                else:
                    raise ValueError("Contextual control must match a complete authored token")
            quoted = set(re.findall(r"[“「]([^”」]+)[”」]", text))
            hints = [{"text": t["text"], "pinyin": t["pinyin"]} for t in entry["tokens"]
                     if t["text"] in quoted and t.get("pinyin") and needs_control(t["text"])]
            add(text, entry["pinyin"], "sentence", quotedReadings=hints,
                **({"contextualReadings": contextual[text]} if text in contextual else {}))
    common_shards = {name: json.loads((args.common_examples / name).read_text())
                     for name in {row['file'] for row in common_index.values()}}
    common_examples = 0
    for vocabulary_id, identity in common_index.items():
        entries = common_shards[identity['file']][vocabulary_id]
        if not entries:
            raise ValueError('Empty common-word example identity: ' + vocabulary_id)
        for entry in entries:
            expected_reading = identity.get('reading_key', '')
            if not valid_common_target(entry, identity['text'], expected_reading, vocabulary_id):
                raise ValueError('Common-word example lacks its exact target proof: ' + vocabulary_id)
            span = entry.get('targetSpan')
            if span:
                compound = entry['tokens'][span['tokenIndex']]
                syllables = compound['pinyin'].split()
                if (not compound.get('vocabularyId')
                        or len(syllables) != len(compound['text'])
                        or not span_reading_matches(identity['text'],
                            ' '.join(syllables[span['start']:span['end']]), expected_reading)):
                    raise ValueError('Bound-form audio target lacks its whole compound/reading proof: ' + vocabulary_id)
            text = ''.join(token['text'] for token in entry['tokens'])
            controls, offset = [], 0
            for token in entry['tokens']:
                end = offset + len(token['text'])
                if token['pinyin']:
                    controls.append({'text': token['text'], 'pinyin': token['pinyin'], 'start': offset, 'end': end})
                offset = end
            if re.sub(r'[^a-züāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]', '', entry['pinyin'].lower()) != re.sub(
                    r'[^a-züāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]', '', ''.join(token['pinyin'] for token in entry['tokens']).lower()):
                raise ValueError('Common-word example token readings disagree with its sentence: ' + vocabulary_id)
            add(text, entry['pinyin'], 'sentence', contextualReadings=controls)
            common_examples += 1
    for item in words:
        pinyin = item["pronunciation"]["value"]
        details = {"practiceWord": item["text"], "practicePinyin": pinyin} if needs_control(item["text"]) else {}
        add(item["text"] + "。", pinyin, "word-practice", **details)
    target = args.output / "catalogue"
    target.mkdir(parents=True, exist_ok=True)
    for prefix in [f'{value:02x}' for value in range(256)]:
        entries = shards[prefix]
        (target / f"{prefix}.json").write_text(
            json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    (args.output / "generation-order.json").write_text(json.dumps(ordered) + "\n")
    print(json.dumps({"voiceVersion": VOICE_VERSION, "assets": len(ordered),
                      "sentences": sum(e["kind"] == "sentence" for s in shards.values() for e in s.values()),
                      "commonWordIdentities": len(common_index), "commonWordExamples": common_examples}))


if __name__ == "__main__":
    main()
