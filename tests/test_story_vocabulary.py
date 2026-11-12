"""Real notebook introductions for exact story prerequisites, before practice."""
import json
from pathlib import Path

import psycopg2
import pytest

from app.email.providers import FakeEmailProvider
from app.main import create_app
from app.services.learning_glosses import learner_translations
from tests.notebook_history import current_introductions
from tests.test_auth_flows import browser, verified_signup

ROOT = Path(__file__).resolve().parents[1]
WORDS = json.loads((ROOT / 'alembic/data/0020_story_vocabulary.json').read_text())
TRACK = 'mandarin-simplified-v1'


@pytest.mark.integration
def test_story_lexemes_are_visible_on_real_prior_cards_and_acknowledged_separately(postgres_config, run_cli):
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT b.id,b.text,b.sequence FROM learning_word_blocks b "
                           "JOIN learning_stages s ON s.id=b.stage_id "
                           "WHERE s.track_id=%s AND b.kind='word' ORDER BY b.sequence", (TRACK,))
            cards = cursor.fetchall()
    mail = FakeEmailProvider()
    with browser(create_app(postgres_config, email_provider=mail)) as client:
        verified_signup(client, mail, 'story-vocabulary@example.com')
        account = client.get('/auth/session').json()['id']
        for word in current_introductions(postgres_config, WORDS):
            block_id, sequence = word['introduction_block_id'], word['introduction_sequence']
            notebook = {'trackId': TRACK, 'completed': [id for id, _, n in cards if n <= sequence], 'exposures': {}}
            displayed = client.post('/learning/guest/lesson', json={**notebook, 'operation': 'vocabulary', 'blockId': block_id})
            assert displayed.status_code == 200, displayed.text
            vocabulary = {item['id']: item for item in displayed.json()['items']}
            assert word['id'] in vocabulary, (word['text'], block_id)
            shown = vocabulary[word['id']]
            expected = word['translations'] if word.get('sense_key') else learner_translations(
                word['translations'], text=word['text'], pronunciation=word['pronunciation'])
            if word['id'] == 'cmn-493e53d9f37773fc8f508f5a':
                expected = {**expected, 'en': 'noun suffix'}
            assert shown['translations']['en'] == expected['en']
            assert shown['pronunciation']['value'] == word['pronunciation']['value']
            assert client.post('/learning/guest/import', json={**notebook, 'accountId': account}).status_code == 200
            url = f'/learning/tracks/{TRACK}/blocks/{block_id}/vocabulary'
            account_display = client.get(url)
            assert account_display.status_code == 200, account_display.text
            assert word['id'] in {item['id'] for item in account_display.json()['items']}
            # Reading a character does not submit the word's distinct exposure.
            with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT count(*) FROM learning_vocabulary_exposures WHERE vocabulary_id=%s', (word['id'],))
                    assert cursor.fetchone()[0] == 0
            references = [item['lexicalUseReference'] for item in account_display.json()['items'] if item['id'] == word['id'] and item.get('lexicalUseReference')]
            ack = client.post(url + '/seen', json={'vocabularyIds': [word['id']], 'lexicalUses': references})
            assert ack.status_code == 200, ack.text
            with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT block_id FROM learning_vocabulary_exposures WHERE vocabulary_id=%s', (word['id'],))
                    assert cursor.fetchall() == [(block_id,)]

        # The simultaneous-use entry is genuinely taught with that sense; the
        # older one-side dictionary entry is not substituted as its exposure.
        simultaneous = next(word for word in WORDS if word['course_word_id'] == 'W-R5-YIBIAN')
        assert simultaneous['translations']['en'] == 'while; marks one of two simultaneous activities'

        # Familiar characters do not expose later grammatical senses during
        # warm-up. The pinned introduction must be reached before display/ack.
        focus = next(word for word in WORDS if word['course_word_id'] == 'W-R5-DE-FOCUS')
        early_id, _, early_sequence = next(card for card in cards if card[1] == '的')
        early = {'trackId': TRACK, 'completed': [id for id, _, n in cards if n <= early_sequence], 'exposures': {}}
        displayed = client.post('/learning/guest/lesson', json={**early, 'operation': 'vocabulary', 'blockId': early_id})
        assert displayed.status_code == 200, displayed.text
        assert focus['id'] not in {item['id'] for item in displayed.json()['items']}
        rejected = client.post(f'/learning/tracks/{TRACK}/blocks/{early_id}/vocabulary/seen',
                               json={'vocabularyIds': [focus['id']]})
        assert rejected.status_code == 422
        forged = client.post('/learning/guest/import', json={**early, 'accountId': account,
                             'exposures': {early_id: [focus['id']]}})
        assert forged.status_code == 422
