"""The authentic meal compound is taught before example use, without rewriting history."""
import json
from pathlib import Path

import psycopg2
import pytest

from app.main import create_app
from tests.test_auth_flows import browser

ROOT = Path(__file__).resolve().parents[1]
TRACK = 'mandarin-simplified-v1'


@pytest.mark.integration
def test_example_vocabulary_forward_repair_preserves_existing_identity(postgres_config, run_cli):
    result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', '0023_online_reading')
    assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,row_to_json(v) FROM learning_vocabulary v ORDER BY id')
            before = dict(cursor.fetchall())
            cursor.execute("SELECT id FROM learning_word_blocks WHERE kind='word' ORDER BY sequence LIMIT 1")
            block = cursor.fetchone()[0]
            cursor.execute("INSERT INTO users (id,created_at,name,email_normalized,failed_logins,failure_window_at,failure_alert_at,new_device_alert_at) VALUES ('example-owner',1,'Reader','examples@example.com',0,0,0,0)")
            cursor.execute("INSERT INTO learning_enrollments (id,user_id,track_id,created_at) VALUES ('example-enrollment','example-owner','mandarin-simplified-v1',1)")
            for word_id in ('cmn-f9589f58b3cd743025baa230', 'cmn-0f209bc6ed49253576c403e4'):
                cursor.execute('INSERT INTO learning_vocabulary_exposures (enrollment_id,vocabulary_id,block_id,displayed_at) VALUES (%s,%s,%s,%s)', ('example-enrollment', word_id, block, 123))
    result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', '0024_example_vocabulary')
    assert result.returncode == 0, result.stderr
    sources = json.loads((ROOT / 'alembic/data/0024_common_word_example_support.json').read_text())
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,row_to_json(v) FROM learning_vocabulary v ORDER BY id')
            after = dict(cursor.fetchall())
            assert set(after) - set(before) == {word['id'] for word in sources}
            corrections = json.loads((ROOT / 'alembic/data/0024_common_word_reading_corrections.json').read_text())
            for item in corrections:
                original = before[item['id']]
                assert original['pronunciation']['value'] == item['previousPinyin']
                before[item['id']] = {**original, 'pronunciation': {**original['pronunciation'], 'value': item['pinyin']}, 'reading_key': item['pinyin']}
            assert {key: after[key] for key in before} == before
            cursor.execute("SELECT b.id,b.sequence FROM learning_word_blocks b JOIN learning_stages s ON s.id=b.stage_id WHERE s.track_id=%s AND b.kind='word' ORDER BY b.sequence", (TRACK,))
            cards = cursor.fetchall()
            cursor.execute("SELECT vocabulary_id,block_id,displayed_at FROM learning_vocabulary_exposures WHERE enrollment_id='example-enrollment' ORDER BY vocabulary_id")
            assert cursor.fetchall() == [(word_id, block, 123) for word_id in sorted(('cmn-f9589f58b3cd743025baa230', 'cmn-0f209bc6ed49253576c403e4'))]
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    card_sequences = dict(cards)
    with browser(create_app(postgres_config)) as client:
        for source in sources:
            earlier_block = next(f'{TRACK}-char-{ord(character):x}' for character in source['characters']
                                 if f'{TRACK}-char-{ord(character):x}' != source['introduction_block_id'])
            for block, sequence in [(earlier_block, card_sequences[earlier_block]),
                                    (source['introduction_block_id'], source['introduction_sequence'])]:
                result = client.post('/learning/guest/lesson', json={'trackId': TRACK, 'completed': [key for key, n in cards if n <= sequence], 'exposures': {}, 'operation': 'vocabulary', 'blockId': block})
                assert result.status_code == 200, result.text
                visible = {item['id']: item for item in result.json()['items']}
                if sequence < source['introduction_sequence']:
                    assert source['id'] not in visible
                else:
                    assert visible[source['id']]['translations'] == source['translations']
                    assert all(visible[source['id']]['pronunciation'][key] == value for key, value in source['pronunciation'].items())
