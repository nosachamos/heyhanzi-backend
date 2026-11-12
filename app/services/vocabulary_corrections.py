"""Forward lexical spelling corrections for current offline content views.

Applied migrations remain immutable. Database corrections use their own frozen
migration receipt; this overlay is for rebuilding current compiler/audio inputs.
"""

import json
from pathlib import Path

CORRECTIONS = Path(__file__).resolve().parents[1] / 'data/learning/vocabulary-corrections.json'


def apply_vocabulary_corrections(rows):
    corrections = {row['id']: row for row in json.loads(CORRECTIONS.read_text())['corrections']}
    result = []
    for row in rows:
        correction = corrections.get(row.get('id'))
        if correction:
            if (row['text'] != correction['text']
                    or row['translations']['en'] != correction['meaning']
                    or row['pronunciation']['value'] not in {correction['previousPinyin'], correction['pinyin']}):
                raise ValueError('Lexical correction no longer matches its exact source: ' + row['id'])
            row = {**row, 'pronunciation': {**row['pronunciation'], 'value': correction['pinyin']}}
        result.append(row)
    return result
