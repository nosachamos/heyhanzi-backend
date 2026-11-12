"""Lexical examples expose the requested sense and only introduced dependencies."""
import json
from pathlib import Path

import psycopg2
import pytest

from app.main import create_app
from app.services.learning_examples import entry_available, example_sentence, valid_common_target
from app.services.learning_glosses import primary_translations, word_translations
from tests.test_auth_flows import browser

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'app/data/learning/common-word-examples'
TRACK = 'mandarin-simplified-v1'


def authored_target(vocabulary_id):
    index = json.loads((DIRECTORY / 'index.json').read_text())['words']
    record = index[vocabulary_id]
    entry = json.loads((DIRECTORY / record['file']).read_text())[vocabulary_id][0]
    identity = {'id': vocabulary_id, 'reading_key': record['reading_key'], 'sense_key': record['sense_key']}
    known = {token['text'] for token in entry['tokens']}
    ids = {token['vocabularyId'] for token in entry['tokens'] if token.get('vocabularyId')}
    if entry.get('targetSpan'):
        ids.add(entry['targetSpan']['vocabularyId'])
        known.add(record['text'])
    return record, identity, entry, known, ids


def render_target(vocabulary_id, *, pronunciation=None, remove_id=None, remove_word=None, identity_changes=None):
    record, identity, entry, known, ids = authored_target(vocabulary_id)
    ids.update(entry.get('requiresVocabularyIds', []))
    identity.update(identity_changes or {})
    return example_sentence(record['text'], pronunciation or {'scheme': 'pinyin', 'value': record['reading_key']},
                            known - {remove_word}, set(record['text']), vocabulary=identity,
                            known_vocabulary_ids=ids - {remove_id}, known_grammar=set(entry.get('requiresGrammar', [])))


def test_identical_hanzi_preserves_the_requested_meaning():
    prediction = render_target('course-lexeme-W-R5-HUI-PREDICT')
    meeting = render_target('course-lexeme-W-SYL-0683')
    assert prediction['english'] == 'It will rain tomorrow.'
    assert meeting['english'] == 'The afternoon meeting starts at three.'
    assert prediction['text'] != meeting['text']
    assert next(token for token in prediction['tokens'] if token['text'] == '会')['meaning'].startswith('will;')
    assert next(token for token in meeting['tokens'] if token['text'] == '会')['meaning'].startswith('meeting;')


def test_wrong_identity_or_reading_cannot_borrow_a_same_written_form_example():
    assert render_target('course-lexeme-W0056', identity_changes={'sense_key': ''}) is None
    assert render_target('course-lexeme-W0056', identity_changes={'id': 'unknown-word'}) is None
    assert render_target('course-lexeme-W0056', pronunciation={'scheme': 'pinyin', 'value': 'bèi'}) is None
    assert render_target('course-lexeme-W0056', pronunciation={'scheme': 'ipa', 'value': 'bēi'}) is None
    assert render_target('course-lexeme-W-R5-YIBIAN', pronunciation={'scheme': 'pinyin', 'value': " yī ' biān "})


def test_only_explicitly_authorized_reading_alternatives_share_an_identity():
    target = 'cmn-63376f5a05fa873d49a8944a'
    assert render_target(target, pronunciation={'scheme': 'pinyin', 'value': 'shú'})
    assert render_target(target, pronunciation={'scheme': 'pinyin', 'value': 'shóu'})
    assert render_target(target, pronunciation={'scheme': 'pinyin', 'value': 'shù'}) is None


@pytest.mark.parametrize(('target', 'pinyin', 'wrong_tone'), [
    ('cmn-4b2360f1eeac776b86f995cc', 'gāoxīn-jìshù', 'gāoxīn-jìshǔ'),
    ('cmn-dac694c3b2526b7102873413', 'hóng-lǜdēng', 'hóng-lǜdèng'),
    ('cmn-436d2385b98c62334e82e354', 'suān-tián-kǔ-là', 'suān-tián-kǔ-lā'),
])
def test_hyphens_are_reading_separators_but_tones_remain_distinct(target, pinyin, wrong_tone):
    assert render_target(target, pronunciation={'scheme': 'pinyin', 'value': pinyin},
                         identity_changes={'reading_key': pinyin})
    assert render_target(target, pronunciation={'scheme': 'pinyin', 'value': wrong_tone},
                         identity_changes={'reading_key': pinyin}) is None


def test_matching_hanzi_does_not_replace_distinct_lexical_exposure():
    target = 'course-lexeme-W-R5-HUI-PREDICT'
    _, _, entry, _, _ = authored_target(target)
    dependency = next(token['vocabularyId'] for token in entry['tokens']
                      if token.get('vocabularyId') and token['vocabularyId'] != target)
    assert render_target(target)
    assert render_target(target, remove_id=target) is None
    assert render_target(target, remove_id=dependency) is None
    assert render_target(target, remove_word='下雨') is None


def test_missing_example_never_fabricates_a_learning_wrapper_or_word_sentence():
    for known in ({'我', '在', '学', '无此词'}, {'无此词'}):
        assert example_sentence('无此词', {'scheme': 'pinyin', 'value': 'wú cǐ cí'}, known,
                                {'无'}, meaning='a missing word') is None


def test_bound_form_keeps_its_whole_compound_and_both_lexical_exposure_requirements():
    target_id, compound_id = 'bound-form-huo', 'compound-huoshi'
    entry = {
        'targetVocabularyId': target_id,
        'tokens': [{'text': '伙食', 'pinyin': 'huǒshí', 'vocabularyId': compound_id}],
        'targetSpan': {'tokenIndex': 0, 'start': 0, 'end': 1, 'text': '伙',
                       'pinyin': 'huǒ', 'vocabularyId': target_id, 'meaning': 'meals'},
    }
    assert valid_common_target(entry, '伙', 'huǒ', target_id)
    assert not valid_common_target(entry, '食', 'shí', target_id)
    assert not valid_common_target(entry, '伙', 'huò', target_id)
    assert not valid_common_target(entry, '伙', 'huǒ', 'another-sense')
    # Knowing each character cannot substitute for the actual compound entry.
    assert not entry_available(entry, {'伙', '食'}, {target_id, compound_id})
    assert not entry_available(entry, {'伙食'}, {target_id})
    assert not entry_available(entry, {'伙食'}, {compound_id})
    assert entry_available(entry, {'伙食'}, {target_id, compound_id})


def test_reviewed_gloss_correction_is_id_scoped_and_never_overwrites_a_distinct_sense():
    correction = next(row for row in json.loads(
        (ROOT / 'app/data/learning/common-word-meaning-corrections-review-b.json').read_text())
        if row['id'] == 'cmn-9c15150a6527bb1b33c26394')
    original = {'en': correction['old'], 'zh': '帅'}
    fixed = word_translations(original, vocabulary_id=correction['id'])
    assert fixed['en'] == 'handsome; (bound form) commander; to lead'
    assert fixed['zh'] == '帅'
    assert original['en'] == correction['old']
    assert word_translations(original, vocabulary_id='another-reading') == original
    assert word_translations(original, vocabulary_id=correction['id'], sense_key='commander') == original
    revised = {'en': 'a later reviewed definition', 'zh': '帅'}
    assert word_translations(revised, vocabulary_id=correction['id']) == revised


def test_compiled_bound_form_requires_the_real_compound_introduction():
    target = 'cmn-b564b1dc55edc7306e6388b7'
    example = render_target(target)
    assert example['text'] == '学校的伙食很好。'
    assert example['targetSpan']['text'] == '伙'
    assert [token['text'] for token in example['tokens']].count('伙食') == 1
    assert not any(token['text'] in {'伙', '食'} for token in example['tokens'])
    assert render_target(target, remove_id='course-lexeme-example-huoshi') is None
    assert render_target(target, remove_word='伙食') is None
    assert render_target(target, remove_id=target) is None


def test_primary_gloss_bridge_preserves_other_readings_and_changed_definitions():
    entries = json.loads((ROOT / 'app/data/learning/common-word-primary-meaning-corrections.json').read_text())
    correction = next(row for row in entries if row['text'] == '只' and row['reading_key'] == 'zhī')
    original = {'en': correction['old'], 'zh': '只'}
    result = primary_translations(original, text='只', pronunciation={'scheme': 'pinyin', 'value': 'zhī'})
    assert result['en'] == correction['new']
    assert 'classifier' in result['en']
    assert original['en'] == correction['old']
    assert primary_translations(original, text='只', pronunciation={'scheme': 'pinyin', 'value': 'zhǐ'}) == original
    updated = {'en': 'a separately reviewed definition', 'zh': '只'}
    assert primary_translations(updated, text='只', pronunciation={'scheme': 'pinyin', 'value': 'zhī'}) == updated


@pytest.mark.integration
def test_guest_notebook_displays_authored_sense_example_without_recording_exposure(postgres_config, run_cli):
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    target = next(word for word in json.loads((ROOT / 'alembic/data/0020_story_vocabulary.json').read_text())
                  if word['id'] == 'course-lexeme-W0056')
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT b.id FROM learning_word_blocks b JOIN learning_stages s ON s.id=b.stage_id "
                           "WHERE s.track_id=%s AND b.kind='word' AND b.sequence<=%s ORDER BY b.sequence",
                           (TRACK, target['introduction_sequence']))
            completed = [row[0] for row in cursor.fetchall()]
    request = {'trackId': TRACK, 'completed': completed, 'exposures': {}, 'operation': 'vocabulary',
               'blockId': target['introduction_block_id']}
    with browser(create_app(postgres_config)) as client:
        response = client.post('/learning/guest/lesson', json=request)
        assert response.status_code == 200, response.text
        shown = next(word for word in response.json()['items'] if word['id'] == target['id'])
        assert shown['example']['text'] == '我喝了一杯水。'
        assert shown['example']['english'] == 'I drank a cup of water.'
        assert next(token for token in shown['example']['tokens'] if token['text'] == '杯')['meaning'] == 'cup; cupful'
        # The read-only response remains reproducible without fabricated exposure.
        assert client.post('/learning/guest/lesson', json=request).json()['items'] == response.json()['items']
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM learning_vocabulary_exposures WHERE vocabulary_id=%s', (target['id'],))
            assert cursor.fetchone()[0] == 0
