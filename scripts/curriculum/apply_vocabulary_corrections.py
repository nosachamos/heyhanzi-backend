"""Write a corrected current vocabulary view without editing historical source files."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services.learning_glosses import (
    plain_english,
    primary_character_display,
    word_translations,
)
from app.services.vocabulary_corrections import apply_vocabulary_corrections


def published_reading_lineage():
    """Explicit migration spellings, never a runtime citation/sandhi inference."""
    directory = Path(__file__).resolve().parents[2] / 'alembic/data'
    result = {}
    for filename in ['0012_curriculum.json', '0014_common_vocabulary.json', '0015_expanded_vocabulary.json',
                     '0016_practical_vocabulary.json', '0020_story_vocabulary.json', '0022_notebook_grammar_vocabulary.json',
                     '0024_common_word_example_support.json', '0026_service_attendant_vocabulary.json',
                     '0027_explanation_noun_vocabulary.json', '0028_notice_noun_vocabulary.json',
                     '0029_introduction_noun.json', '0030_context_vocabulary.json']:
        path = directory / filename
        if not path.exists():
            continue
        document = json.loads(path.read_text())
        rows = document if isinstance(document, list) else document.get('vocabulary', [])
        for row in rows:
            if row.get('pronunciation', {}).get('scheme') == 'pinyin':
                result.setdefault(row['id'], set()).add((row['text'], row['pronunciation']['value']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compact', action='store_true', help='Keep public redistribution JSON compact')
    parser.add_argument('--notebook-order', type=Path, help='Explicit current stable-ID notebook order for redistribution anchors')
    parser.add_argument('--current-introductions', type=Path, help='Frozen current lexical display anchors from migration 0036')
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Use a separate output; historical inputs remain unchanged')
    document = json.loads(args.input.read_text())
    if isinstance(document, list):
        corrected = apply_vocabulary_corrections(document)
    else:
        corrected = {**document, 'vocabulary': apply_vocabulary_corrections(document['vocabulary'])}
        if 'characters' in document:
            corrected['characters'] = [primary_character_display(row) for row in document['characters']]
            note = 'Corrected first-taught primary character readings and senses through forward migration 0034; original source records and learner history retained.'
            provenance = dict(document.get('provenance', {}))
            adaptation = provenance.get('adaptation', '')
            if note not in adaptation:
                provenance['adaptation'] = (adaptation + ' ' + note).strip()
            corrected['provenance'] = provenance
    rows = corrected if isinstance(corrected, list) else corrected['vocabulary']
    for row in rows:
        row['translations'] = word_translations(row['translations'], vocabulary_id=row['id'],
            text=row['text'], pronunciation=row['pronunciation'], sense_key=row.get('sense_key', ''))
    if args.notebook_order:
        if isinstance(corrected, list):
            parser.error('--notebook-order requires a curriculum document')
        order = json.loads(args.notebook_order.read_text())
        cards = {row['blockId']: row for row in order['cards']}
        for row in corrected.get('characters', []):
            display = row.get('primary_lexical_display')
            if display:
                card = cards.get(display['introduction_block_id'])
                if card is None:
                    raise ValueError('Current primary redistribution anchor changed: ' + row['text'])
                display['introduction_block_sequence'] = card['sequence']
    if args.current_introductions:
        if not args.notebook_order:
            parser.error('--current-introductions requires --notebook-order')
        introductions = json.loads(args.current_introductions.read_text())
        by_id = {row['source_id']: row for row in introductions['items']}
        lineage = published_reading_lineage()
        reading_updates = []
        for row in rows:
            intro = by_id.get(row['id'])
            if intro is None:
                continue
            meanings = {intro['source_meaning'], intro.get('current_meaning', intro['source_meaning'])}
            meanings.update(plain_english(value) for value in list(meanings))
            current_meaning = word_translations(
                {'en': intro.get('current_meaning', intro['source_meaning'])}, vocabulary_id=row['id'],
                text=intro['text'], pronunciation={'scheme': 'pinyin', 'value': intro['source_pinyin']},
                sense_key=row.get('sense_key', ''))['en']
            meanings.add(current_meaning)
            if row['pronunciation']['value'] != intro['source_pinyin']:
                spellings = lineage.get(row['id'], set())
                if ((row['text'], row['pronunciation']['value']) not in spellings
                        or (intro['text'], intro['source_pinyin']) not in spellings):
                    raise ValueError('Undocumented lexical redistribution reading change: ' + row['id'])
                reading_updates.append({'source_id': row['id'], 'text': row['text'],
                    'previous': row['pronunciation']['value'], 'current': intro['source_pinyin'],
                    'source': 'Explicit frozen vocabulary migration lineage'})
                row['pronunciation'] = {**row['pronunciation'], 'value': intro['source_pinyin']}
            if (row['text'] != intro['text'] or row['pronunciation']['value'] != intro['source_pinyin']
                    or row['translations']['en'] not in meanings
                    or cards[intro['block']]['sequence'] != intro['sequence']):
                raise ValueError('Current lexical redistribution identity changed: ' + row['id'])
            row.update(introduction_block_id=intro['block'], introduction_sequence=intro['sequence'])
            row['translations'] = {**row['translations'], 'en': current_meaning}
        provenance = corrected.setdefault('provenance', {})
        provenance['current_notebook_order_revision'] = order['revision']
        retained_updates = {tuple(item[key] for key in ['source_id', 'previous', 'current']): item
            for item in provenance.get('current_reading_corrections', []) + reading_updates}
        provenance['current_reading_corrections'] = list(retained_updates.values())
        provenance['current_projection'] = 'Existing redistribution scope; exact source-ID meaning corrections and current stable notebook anchors. Personal exposure and mastery are separate.'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(corrected, ensure_ascii=False,
                                    **({'separators': (',', ':')} if args.compact else {'indent': 2})) + '\n')


if __name__ == '__main__':
    main()
