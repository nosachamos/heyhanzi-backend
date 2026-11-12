"""Exercise the public migration and HTTP boundaries for character geometry."""
import hashlib
import json

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

from app.email.providers import FakeEmailProvider
from app.main import create_app
from tests.learner_journey import complete_required_before
from tests.test_auth_flows import browser, verified_signup

pytestmark = pytest.mark.integration


def test_complete_stroke_upgrade_preserves_learning_and_published_resources(postgres_config, run_cli):
    result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', '0017_billing')
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    with browser(create_app(postgres_config, email_provider=mail)) as client:
        verified_signup(client, mail, 'stroke-upgrade@example.com')
        track = 'mandarin-simplified-v1'
        path_url = f'/learning/tracks/{track}/path'
        # The pre-0018 schema predates pinyin_completed. Seed its historical
        # rows at the database boundary, then exercise current HTTP after head.
        user_id = client.get('/auth/session').json()['id']
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO learning_enrollments (id,user_id,track_id,created_at) VALUES (%s,%s,%s,1)",
                               ('stroke-enrollment', user_id, track))
                cursor.execute("SELECT b.id FROM learning_word_blocks b JOIN learning_stages s ON s.id=b.stage_id WHERE s.track_id=%s AND s.position=1 ORDER BY b.position LIMIT 4", (track,))
                first_ids = [row[0] for row in cursor.fetchall()]
                for block_id in first_ids[:3]:
                    cursor.execute("INSERT INTO learning_word_completions (enrollment_id,block_id,completed_at) VALUES (%s,%s,123)",
                                   ('stroke-enrollment', block_id))
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute('SELECT * FROM learning_word_blocks ORDER BY id')
                old_words = cursor.fetchall()
                original_refs = {row['id']: row['language_data'].get('strokeOrder') for row in old_words}
                cursor.execute('SELECT * FROM learning_character_strokes ORDER BY character, version')
                old_resources = cursor.fetchall()
                cursor.execute('SELECT * FROM learning_word_completions ORDER BY block_id')
                old_completions = cursor.fetchall()

        for _ in range(2):
            result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', '0018_all_character_strokes')
            assert result.returncode == 0, result.stderr
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute('SELECT * FROM learning_word_blocks ORDER BY id')
                new_words = cursor.fetchall()
                covered, references = set(), {}
                for old, new in zip(old_words, new_words, strict=True):
                    if new['language_data'].get('kind') == 'mandarin-simplified':
                        refs = new['language_data'].pop('strokeOrder')
                        assert [ref['character'] for ref in refs] == list(dict.fromkeys(new['text']))
                        old['language_data'].pop('strokeOrder', None)
                        for ref in refs:
                            assert ref['format'] == 'stroke-reference-v1'
                            assert ref['languageTag'] == 'cmn-Hans-CN'
                            covered.add(ref['character'])
                            references[ref['character']] = ref['version']
                    assert old == new
                assert len(covered) == 1940
                cursor.execute('SELECT * FROM learning_word_completions ORDER BY block_id')
                assert cursor.fetchall() == old_completions
                cursor.execute('SELECT * FROM learning_character_strokes ORDER BY character, version')
                matched, retained = set(), []
                for resource in cursor:
                    if resource in old_resources:
                        retained.append(resource)
                    if references.get(resource['character']) != resource['version']:
                        continue
                    data = resource['data']
                    assert data['format'] == 'hanzi-writer-v1'
                    assert data['character'] == resource['character']
                    assert len(data['strokes']) == len(data['medians']) > 0
                    assert all(len(median) >= 2 for median in data['medians'])
                    assert hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                                    separators=(',', ':')).encode()).hexdigest() == resource['version']
                    matched.add(resource['character'])
                assert matched == covered
                assert retained == old_resources


        result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', 'head')
        assert result.returncode == 0, result.stderr
        after = client.get(path_url).json()
        assert after['completedCount'] == 3
        current_words = [b for stage in after['stages'] for b in stage['blocks'] if b['kind'] == 'word']
        assert after['nextBlockId'] == next(b['id'] for b in current_words if b['id'] not in first_ids[:3])
        after_blocks = {b['id']: b for stage in after['stages'] for b in stage['blocks']}
        old_by_id = {row['id']: row for row in old_words}
        for block_id in first_ids:
            current = after_blocks[block_id]
            assert current['status'] == ('completed' if block_id in first_ids[:3] else 'next' if block_id == after['nextBlockId'] else 'locked')
            for key in ['scheme', 'value']:
                assert current['pronunciation'][key] == old_by_id[block_id]['pronunciation'][key]
            if original_refs[block_id]:
                assert current['languageData']['strokeOrder'] == original_refs[block_id]


def test_strokes_are_available_on_every_warmup_card_and_first_hsk_card(billing_app):
    app, mail, stripe = billing_app
    with browser(app) as client:
        verified_signup(client, mail, 'complete-strokes@example.com')
        assert client.post('/billing/checkout', json={}).status_code == 200
        stripe.pay()
        assert stripe.event(client).status_code == 200
        track = 'mandarin-simplified-v1'
        path = client.post('/learning/enrollments', json={'trackId': track}).json()
        warmup = path['stages'][0]['blocks']

        def fetch_strokes(block):
            refs = block['languageData']['strokeOrder']
            assert [ref['character'] for ref in refs] == list(dict.fromkeys(block['text']))
            for ref in refs:
                url = f'/learning/strokes/{ref["languageTag"]}/{ref["character"]}/{ref["version"]}'
                response = client.get(url, params={'blockId': block['id']})
                assert response.status_code == 200, response.text
                assert response.headers['cache-control'] == 'no-store'
                assert response.json()['reference'] == ref
                geometry = response.json()['data']
                assert geometry['character'] == ref['character']
                assert geometry['format'] == 'hanzi-writer-v1'
                assert len(geometry['strokes']) == len(geometry['medians']) > 0

        locked = warmup[-1]
        ref = locked['languageData']['strokeOrder'][0]
        assert client.get(f'/learning/strokes/{ref["languageTag"]}/{ref["character"]}/{ref["version"]}',
                          params={'blockId': locked['id']}).status_code == 409
        for block in warmup:
            complete_required_before(client, block['id'])
            fetch_strokes(block)
            assert client.post(f'/learning/tracks/{track}/blocks/{block["id"]}/complete', json={}).status_code == 200
        assert any(block['text'] == '中文' and len(block['languageData']['strokeOrder']) == 2 for block in warmup)
        complete_required_before(client, 'warmup-end')
        hsk = client.get(f'/learning/tracks/{track}/journey?level=hsk-1').json()
        current = next(block for stage in hsk['stages'] for block in stage['blocks'] if block['id'] == hsk['nextBlockId'])
        assert current['kind'] == 'word' and current['status'] == 'next'
        fetch_strokes(current)
