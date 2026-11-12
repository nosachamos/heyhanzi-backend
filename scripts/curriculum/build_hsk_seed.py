"""Build the frozen character-first prototype from pinned, licensed HSK/CC-CEDICT data.

Run with the dictionary archive recorded in 0012_sources/sources.json. The API
never reads these inputs or downloads a dictionary in a request.
"""
import argparse
import gzip
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'alembic/data/0012_sources'


def tone_marks(pinyin):
    result = []
    for syllable in pinyin.replace('u:', 'ü').split():
        match = re.fullmatch(r'([a-zA-ZüÜ]+)([1-5])', syllable)
        if not match:
            result.append(syllable)
            continue
        word, tone = match[1].lower(), int(match[2])
        if tone < 5:
            index = next((word.index(v) for v in ('a', 'e') if v in word), None)
            if index is None:
                index = word.index('o') if 'ou' in word else max((i for i, c in enumerate(word) if c in 'iouü'), default=-1)
            if index >= 0:
                tables = {'a': 'āáǎà', 'e': 'ēéěè', 'i': 'īíǐì', 'o': 'ōóǒò', 'u': 'ūúǔù', 'ü': 'ǖǘǚǜ'}
                word = word[:index] + tables[word[index]][tone - 1] + word[index + 1:]
        result.append(word)
    return ' '.join(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dictionary', required=True, type=Path)
    args = parser.parse_args()
    provenance = json.loads((SOURCES / 'sources.json').read_text())
    assert hashlib.sha256(args.dictionary.read_bytes()).hexdigest() == provenance['sources'][-1]['sha256']
    entries = defaultdict(list)
    for line in gzip.open(args.dictionary, 'rt', encoding='utf-8'):
        match = re.match(r'\S+ (\S+) \[([^]]+)\] /(.+)/', line)
        if match:
            text, reading, meanings = match.groups()
            definitions = [v for v in meanings.split('/') if not v.startswith(('CL:', 'variant of', 'see ', 'old variant', 'surname '))]
            entries[text].append((tone_marks(reading), definitions, not reading[0].isupper()))
    words = {}
    for level in range(1, 7):
        for row in (SOURCES / f'vocabulary-{level}.tsv').read_text().splitlines():
            values = row.split('\t')
            if len(values) < 8:
                continue
            text, pinyin = values[0], values[2]
            if not text or not all('\u3400' <= ch <= '\u9fff' for ch in text):
                continue
            key = text + '|' + pinyin
            if key in words:
                continue
            dictionary = sorted(entries[text], key=lambda item: (item[0].replace(' ', '') != pinyin.replace(' ', ''), not item[2], not bool(item[1])))
            definitions = dictionary[0][1] if dictionary else [html.unescape(v) for v in re.findall(r'<li>(.*?)</li>', values[7])]
            gloss = '; '.join(definitions[:2])[:240]
            if not gloss:
                continue
            words[key] = {'id': 'cmn-' + hashlib.sha256(key.encode()).hexdigest()[:24], 'text': text, 'level': level,
                          'translations': {'en': gloss, 'zh': text}, 'pronunciation': {'scheme': 'pinyin', 'value': pinyin},
                          'characters': list(dict.fromkeys(text)), 'rank': int(values[6] or 0)}
    # Explicit common everyday combinations are editorial choices, not frequency claims.
    for text, pinyin, english, chinese in [('老师', 'lǎo shī', 'teacher', '教师'), ('老公', 'lǎo gōng', 'husband (informal)', '丈夫（口语）'), ('老婆', 'lǎo pó', 'wife (informal)', '妻子（口语）'), ('老人', 'lǎo rén', 'older person', '年长的人')]:
        key = text + '|' + pinyin
        item = words.get(key, {'id': 'cmn-' + hashlib.sha256(key.encode()).hexdigest()[:24], 'text': text, 'level': 3,
                             'pronunciation': {'scheme': 'pinyin', 'value': pinyin}, 'characters': list(text)})
        item.update(translations={'en': english, 'zh': chinese}, rank=10**10)
        words[key] = item
    # A character has its own reading/meaning; compound readings remain lexical data.
    warmup = json.loads((ROOT / 'alembic/data/0004_warmup.json').read_text())
    seen = {char for word in warmup['words'] for char in word['language_data']['characters']}
    characters = []
    missing = []
    for level in range(1, 7):
        available = list(dict.fromkeys((SOURCES / f'characters-{level}.txt').read_text().split()))
        ranks = {char: sum(word['rank'] for word in words.values() if char in word['characters'] and word['level'] <= level) for char in available}
        available.sort(key=lambda char: (0 if level == 1 and char in '老师公' else 1, '老师公'.find(char) if level == 1 and char in '老师公' else -ranks[char], char))
        for char in available:
            if char in seen:
                continue
            seen.add(char)
            dictionary = sorted(entries[char], key=lambda item: (not item[2], not bool(item[1])))
            single = next((word for word in words.values() if word['text'] == char and word['level'] <= level), None)
            if single:
                reading, gloss = single['pronunciation']['value'], single['translations']['en']
            elif dictionary:
                reading, definitions, _ = dictionary[0]
                gloss = '; '.join(definitions[:2])[:240]
            else:
                missing.append(char)
                continue
            characters.append({'text': char, 'level': level, 'translations': {'en': gloss, 'zh': char},
                               'pronunciation': {'scheme': 'pinyin', 'value': reading}})
    if missing:
        raise ValueError(f'Missing character definitions: {missing}')
    result = {'provenance': provenance, 'characters': characters, 'vocabulary': list(words.values())}
    (ROOT / 'alembic/data/0012_curriculum.json').write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    print('Character counts:', {n: sum(char['level'] == n for char in characters) for n in range(1, 7)})
    print('Vocabulary entries:', len(words))


if __name__ == '__main__':
    main()
