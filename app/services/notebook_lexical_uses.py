"""Exact visible lexical-scope teaching, separate from old dictionary exposure.

All use rows are rendered together in the existing Common words entry. A client
receipt binds that complete authored scope to a fingerprint; it is not mastery.
The progress boundary also requires the exact supporting lexical displays.
"""
import hashlib
import json

from app.services import learning_glosses
from app.services.sentence_audio import reference as audio_reference


def authored(item):
    return item.language_data.get('lexical_uses', [])


def corrected_heading(item):
    """Only an active exact-ID correction needs a new heading display."""
    correction = learning_glosses.word_meaning_corrections().get(item.id)
    if not correction or item.sense_key:
        return None
    if (correction.get('text') and item.text != correction['text']) or (
            correction.get('reading') and (item.pronunciation.get('scheme') != 'pinyin'
                or learning_glosses._reading_key(item.pronunciation.get('value', ''))
                != learning_glosses._reading_key(correction['reading']))):
        return None
    displayed = learning_glosses.word_translations(item.translations,
        vocabulary_id=item.id, text=item.text, pronunciation=item.pronunciation,
        sense_key=item.sense_key)
    return displayed['en'] if displayed.get('en') == correction['new'] else None


def requires_display(item):
    return bool(authored(item) or corrected_heading(item))


def requirements(item):
    """Union of exact token/support identities for the complete visible scope."""
    result = {}
    for use in authored(item):
        for requirement in use['lexical_requirements']:
            previous = result.get(requirement['id'])
            if previous is not None and previous != requirement:
                raise ValueError('Conflicting lexical-use prerequisite')
            result[requirement['id']] = requirement
    return list(result.values())


def reference(item):
    uses = authored(item)
    heading = corrected_heading(item)
    if not uses and not heading:
        return None
    contract = {'source_id': item.id, 'text': item.text, 'pinyin': item.pronunciation['value'],
                'meaning': item.translations,
                'revision': item.language_data['core_vocabulary_revision'] if uses else 0,
                'uses': uses}
    if heading:
        contract['current_heading'] = {'en': heading,
            'policy': 'exact-reviewed-heading-display-v1'}
    fingerprint = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    return {'id': item.id, 'fingerprint': fingerprint}


def public_uses(item):
    result = []
    for use in authored(item):
        row = {key: use[key] for key in ['id', 'explanation', 'context']}
        if use.get('example'):
            example = use['example']
            row['example'] = {**example, 'audio': audio_reference(example['text'], example['pinyin'],
                word_texts=[token['text'] for token in example['tokens'] if token.get('pinyin')])}
        result.append(row)
    return result


def support_ids(item):
    return {requirement['source_id'] for requirement in requirements(item)
            if requirement['source_id'] != item.id}
