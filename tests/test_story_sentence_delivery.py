"""Public story definition validation accepts mixed whole and sentence media."""
import copy

import pytest

from app.services.course_story import validate_definition


def story():
    choices = [{'id': 'yes', 'correct': True}, {'id': 'no', 'correct': False}]
    paragraph = {'id': 'p', 'tokens': [
        {'id': 'a', 'text': '你好', 'pinyin': ['nǐ', 'hǎo']}, {'id': 'b', 'text': '。', 'pinyin': []},
        {'id': 'c', 'text': '再见', 'pinyin': ['zài', 'jiàn']}, {'id': 'd', 'text': '。', 'pinyin': []}],
        'question': {'id': 'q', 'choices': choices}}
    reference = {'audio': {'url': '/exercise-assets/audio/clip.mp3', 'sha256': 'a' * 64, 'byteLength': 42, 'durationSeconds': 1.2},
                 'alignment': {'url': '/exercise-assets/stories/timing.json', 'sha256': 'b' * 64, 'byteLength': 42}}
    delivery = {'clips': [
        {'id': 's1', 'firstTokenId': 'a', 'lastTokenId': 'b', 'reference': copy.deepcopy(reference), 'pauseAfterSeconds': .25},
        {'id': 's2', 'firstTokenId': 'c', 'lastTokenId': 'd', 'reference': copy.deepcopy(reference)}]}
    return {'definition': {'id': 'ACT-STORY-COMP', 'steps': [
        {'id': 'read', 'kind': 'reading'}, {'id': 'check', 'kind': 'check', 'questions': [
            {'id': 'q', 'kind': 'choice', 'choices': choices}]}],
        'storyActivity': {'mode': 'read', 'sample': {'paragraphs': [paragraph]},
                          'recordings': {'paragraphs': {'p': {'female': reference, 'male': delivery}}}}}}


def test_mixed_whole_and_sentence_delivery_preserves_story_definition():
    item = story()
    before = copy.deepcopy(item)
    validate_definition(item)
    assert item == before


def test_final_existing_colon_cue_is_allowed_but_not_an_internal_split():
    item = story()
    tokens = item['definition']['storyActivity']['sample']['paragraphs'][0]['tokens']
    tokens[-1]['text'] = '：'
    validate_definition(item)
    tokens[1]['text'] = '：'
    with pytest.raises(ValueError, match='sentence delivery'):
        validate_definition(item)


@pytest.mark.parametrize('change', ['overlap', 'omit', 'nested', 'voice', 'long-pause', 'trailing-pause', 'nonfinite', 'mixed'])
def test_rejects_invalid_sentence_delivery_from_catalog(change):
    item = story()
    delivery = item['definition']['storyActivity']['recordings']['paragraphs']['p']['male']
    if change == 'overlap':
        delivery['clips'][1]['firstTokenId'] = 'b'
    elif change == 'omit':
        delivery['clips'][1]['lastTokenId'] = 'c'
    elif change == 'nested':
        delivery['clips'][0]['reference'] = {'clips': []}
    elif change == 'voice':
        delivery['clips'][0]['voice'] = 'female'
    elif change == 'long-pause':
        delivery['clips'][0]['pauseAfterSeconds'] = 1.5
    elif change == 'trailing-pause':
        delivery['clips'][1]['pauseAfterSeconds'] = .25
    elif change == 'nonfinite':
        delivery['clips'][0]['reference']['audio']['durationSeconds'] = float('nan')
    else:
        delivery['audio'] = {}
    with pytest.raises(ValueError, match='sentence delivery'):
        validate_definition(item)
