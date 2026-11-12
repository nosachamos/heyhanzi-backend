"""Local jump-ahead requests stay separate from durable learner evidence."""
import copy
import json
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app.email.providers import FakeEmailProvider
from app.main import create_app
from tests.test_auth_flows import browser, verified_signup
from tests.test_course_exercises import MANIFEST, check_payload, save_payload

HEADER = {'X-HeyHanzi-Development-Preview': '1'}
NOTEBOOK = {'trackId': 'mandarin-simplified-v1', 'completed': [], 'exposures': {}}


@pytest.mark.parametrize('environment', ['TEST', 'PROD'])
def test_preview_header_is_rejected_outside_development(app_config, environment):
    app_config['GENERAL']['Environment'] = environment
    if environment == 'PROD':
        app_config['DATABASE']['Url'] = 'postgresql://test:test@db.example.com/app?sslmode=require'
        app_config['EMAIL'] = {'Provider': 'ses', 'FromEmail': 'no-reply@heyhanzi.com',
                               'FromName': 'HeyHanzi', 'SupportUrl': 'https://heyhanzi.com', 'SesRegion': 'us-east-1'}
        app_config['TURNSTILE'] = {'SiteKey': 'production-site-fixture', 'SecretKey': 'production-secret-fixture',
                                   'Hostnames': ['heyhanzi.com']}
        app_config['AWS'] = {'AccessKeyId': 'fixture-access', 'SecretAccessKey': 'fixture-secret'}
    client = TestClient(create_app(app_config))
    for path in ['/learning/guest/lesson', '/learning/course/guest']:
        response = client.post(path, json={}, headers=HEADER)
        assert response.status_code == 403
        assert response.json()['detail']['code'] == 'development_preview_unavailable'
        assert response.headers['cache-control'] == 'no-store'


@pytest.mark.integration
def test_development_preview_unlocks_content_without_writing_or_crossing_modes(postgres_config, run_cli):
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    config = copy.deepcopy(postgres_config)
    config['GENERAL']['Environment'] = 'DEV'
    config['ADMIN'] = {'Emails': ['preview@example.com']}
    mail = FakeEmailProvider()
    with browser(create_app(config, email_provider=mail)) as client:
        verified_signup(client, mail, 'preview@example.com')
        account = client.get('/auth/session').json()['id']
        preview_notebook = {**NOTEBOOK, 'developmentPreview': True}
        response = client.post('/learning/guest/lesson', json={**preview_notebook, 'level': 'hsk-6'}, headers=HEADER)
        assert response.status_code == 200, response.text
        journey = response.json()
        assert journey['completedCount'] == 0 and journey['hasUnlimitedAccess']
        assert journey['nextBlockId'].endswith('-warmup-wo')
        blocks = [block for stage in journey['stages'] for block in stage['blocks'] if block['kind'] == 'word']
        assert blocks and all(block['status'] == 'next' for block in blocks)
        future = blocks[0]
        for operation, fields in [('vocabulary', {}), ('strokes', {'reference': future['languageData']['strokeOrder'][0]})]:
            response = client.post('/learning/guest/lesson', json={**preview_notebook,
                'operation': operation, 'blockId': future['id'], **fields}, headers=HEADER)
            assert response.status_code == 200, response.text
            assert response.headers['cache-control'] == 'no-store'
        normal = client.post('/learning/guest/lesson', json={**NOTEBOOK, 'level': 'hsk-6'}).json()
        assert not normal['hasUnlimitedAccess']
        assert all(block['status'] == 'locked' for stage in normal['stages'] for block in stage['blocks'])
        assert client.post('/learning/guest/lesson', json={**NOTEBOOK, 'operation': 'vocabulary',
            'blockId': future['id']}).status_code == 409
        assert client.post('/learning/guest/lesson', json={**preview_notebook, 'completed': ['unknown']}, headers=HEADER).status_code == 422
        assert client.post('/learning/guest/lesson', json=preview_notebook).status_code == 422
        grammar = json.loads((Path(__file__).resolve().parents[1] / 'app/data/learning/notebook-grammar.json').read_text())
        note = next(item for item in grammar['items'] if item['requires_grammar'])
        response = client.post('/learning/guest/lesson', json={**preview_notebook, 'operation': 'vocabulary',
            'blockId': note['block_id']}, headers=HEADER)
        assert response.status_code == 200, response.text
        assert note['id'] in {item['id'] for item in response.json()['grammarNotes']}

        def course(operation, progress=None, **fields):
            response = client.post('/learning/course/guest', headers=HEADER, json={
                'operation': operation, 'notebook': preview_notebook, 'progress': progress or {'runs': []}, **fields})
            assert response.status_code == 200, response.text
            assert response.json()['progress']['developmentPreview'] is True
            return response.json()

        state = course('state')
        assert set(state['eligible']) == {item['id'] for item in MANIFEST['exercises']}
        assert state['completed'] == [] and state['grammarReviews'] == {}
        story = next(item for item in reversed(MANIFEST['exercises']) if item['definition']['id'] in {'ACT-STORY', 'ACT-STORY-COMP'})
        assert course('exercise', instanceId=story['id'])['exercise']['id'] == story['id']
        item = next(item for item in reversed(MANIFEST['exercises']) if item['definition']['id'] == 'ACT-CLOZE')
        run_id = str(uuid4())
        saved = course('save', runId=run_id, save=save_payload(item))
        checked = course('check', saved['progress'], runId=run_id, check=check_payload(item))
        assert checked['run']['status'] == 'passed'
        assert course('exercise', checked['progress'], instanceId=item['id'])['run']['id'] == run_id
        assert course('state', checked['progress'])['completed'] == [item['id']]
        assert course('advance', checked['progress'], blockId=future['id'])['allowed']
        assert client.post('/learning/course/guest', json={'operation': 'state',
            'notebook': NOTEBOOK, 'progress': checked['progress']}).status_code == 422
        assert client.post('/learning/course/guest', json={'operation': 'advance',
            'notebook': NOTEBOOK, 'progress': checked['progress'], 'blockId': journey['nextBlockId']}).status_code == 422
        assert client.post('/learning/course/import', json={'accountId': account,
            'progress': checked['progress']}).status_code == 422
        assert client.post('/learning/guest/import', json={**preview_notebook, 'accountId': account}).status_code == 422
        for path in ['/learning/guest/import', '/learning/course/import', '/learning/enrollments']:
            response = client.post(path, json={}, headers=HEADER)
            assert response.status_code == 403
            assert response.json()['detail']['code'] == 'development_preview_unavailable'
        assert client.get('/learning/enrollments').json()['trackIds'] == []
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor() as cursor:
                for table in ['learning_enrollments', 'learning_word_completions', 'learning_vocabulary_exposures',
                              'learning_grammar_exposures', 'learning_course_exercise_runs']:
                    from psycopg2 import sql
                    cursor.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table)))
                    assert cursor.fetchone()[0] == 0


@pytest.mark.integration
def test_local_preview_requires_the_shared_admin_role(postgres_config, run_cli):
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    config = copy.deepcopy(postgres_config)
    config['GENERAL']['Environment'] = 'DEV'
    config['ADMIN'] = {'Emails': ['owner@example.com']}
    mail = FakeEmailProvider()
    with browser(create_app(config, email_provider=mail)) as client:
        body = {**NOTEBOOK, 'developmentPreview': True, 'level': 'hsk-6'}
        assert client.post('/learning/guest/lesson', json=body, headers=HEADER).status_code == 401
        verified_signup(client, mail, 'non-admin@example.com')
        assert client.post('/learning/guest/lesson', json=body, headers=HEADER).status_code == 403
        assert client.get('/auth/session').json()['role'] == 'learner'
        assert client.post('/learning/guest/lesson', json={**NOTEBOOK, 'level': 'warmup'}).status_code == 200
