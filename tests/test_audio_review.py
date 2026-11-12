"""Real HTTP decisions on disposable PostgreSQL; no auth/database stubs."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.main import create_app
from app.services.audio_review import active_catalog, recording
from tests.test_auth_flows import (
    PASSWORD,
    browser,
    email_token,
    signup,
    verified_signup,
    wait_email,
)

pytestmark = pytest.mark.integration
REVIEWER = 'audio-reviewer@example.com'


def clip():
    key = active_catalog()['keys'][0]
    return key, recording(key)


@pytest.fixture
def review_app(postgres_config, run_cli):
    from app.email.providers import FakeEmailProvider
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    postgres_config['ADMIN'] = {'Emails': [REVIEWER]}
    postgres_config['AUDIO_REVIEW'] = {'ReviewerEmails': ['not-a-reviewer@example.com']}
    mail = FakeEmailProvider()
    return create_app(postgres_config, email_provider=mail), mail


def test_review_permissions_and_csrf_fail_closed(review_app):
    app, mail = review_app
    key, source = clip()
    payload = {'approved': True, 'expectedRevision': 0, 'sha256': source['sha256']}
    with browser(app) as client:
        assert client.get('/learning/audio-review/state').status_code == 401
        assert client.put(f'/learning/audio-review/recordings/{key}', json=payload).status_code == 401
        signup(client, REVIEWER)
        token = email_token(wait_email(mail, 'welcome_verify'))
        assert client.put(f'/learning/audio-review/recordings/{key}', json=payload).status_code == 403
    with browser(app) as client:
        verified_signup(client, mail, 'not-a-reviewer@example.com')
        assert client.get('/learning/audio-review/access').status_code == 403
        assert client.put(f'/learning/audio-review/recordings/{key}', json=payload).status_code == 403
    with browser(app) as client:
        # Reuse and verify the original account rather than signing it up twice.
        login = client.post('/auth/email/login', json={'email': REVIEWER, 'password': PASSWORD})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.headers['X-CSRF-Token']
        assert client.post('/auth/email/verify', json={'token': token}).status_code == 200
        client.headers['X-CSRF-Token'] = 'wrong'
        assert client.put(f'/learning/audio-review/recordings/{key}', json=payload).status_code == 403
        client.headers['X-CSRF-Token'] = login.headers['X-CSRF-Token']
        assert client.put(f'/learning/audio-review/recordings/{key}', json=payload,
                          headers={'Origin': 'https://untrusted.example'}).status_code == 403


def test_approval_survives_new_app_and_undo_preserves_versions(review_app):
    app, mail = review_app
    key, source = clip()
    url = f'/learning/audio-review/recordings/{key}'
    payload = {'approved': True, 'expectedRevision': 0, 'sha256': source['sha256']}
    with browser(app) as client:
        verified_signup(client, mail, REVIEWER)
        assert client.get('/learning/audio-review/access').json()['canReview']
        result = client.put(url, json=payload)
        assert result.status_code == 200
        assert result.json()['approved'] and result.json()['revision'] == 1
        assert result.headers['Cache-Control'] == 'no-store'
        assert client.put(url, json=payload).status_code == 409
        assert client.put(url, json={**payload, 'sha256': '0' * 64, 'expectedRevision': 1}).status_code == 409
        assert client.put('/learning/audio-review/recordings/' + '0' * 64, json=payload).status_code == 404
    # A fresh application and authenticated admin see committed shared metadata.
    replacement = create_app(app.state.config, email_provider=mail)
    with browser(replacement) as client:
        login = client.post('/auth/email/login', json={'email': REVIEWER, 'password': PASSWORD})
        client.headers['X-CSRF-Token'] = login.headers['X-CSRF-Token']
        state = client.get('/learning/audio-review/state')
        assert state.headers['Cache-Control'] == 'no-store'
        assert state.json()['decisions'][key]['approved']
        assert set(state.json()['decisions'][key]) == {'approved', 'revision', 'reviewedAt'}
        login = client.post('/auth/email/login', json={'email': REVIEWER, 'password': PASSWORD})
        client.headers['X-CSRF-Token'] = login.headers['X-CSRF-Token']
        result = client.put(url, json={**payload, 'approved': False, 'expectedRevision': 1})
        assert result.status_code == 200 and result.json()['revision'] == 2
        assert not client.get('/learning/audio-review/state').json()['decisions'][key]['approved']
        other = next(k for k in active_catalog()['keys'] if recording(k)['voice'] != source['voice'])
        assert other not in client.get('/learning/audio-review/state').json()['decisions']


def test_concurrent_reviews_cannot_overwrite_a_stale_decision(review_app):
    app, mail = review_app
    key, source = clip()
    with browser(app) as client:
        verified_signup(client, mail, REVIEWER)
        payload = {'approved': True, 'expectedRevision': 0, 'sha256': source['sha256']}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: client.put(f'/learning/audio-review/recordings/{key}', json=payload).status_code,
                                    range(2)))
        assert sorted(results) == [200, 409]
        assert client.get('/learning/audio-review/state').json()['decisions'][key]['revision'] == 1


def test_admin_role_is_shared_and_cannot_be_self_assigned(review_app):
    app, mail = review_app
    with browser(app) as client:
        verified_signup(client, mail, REVIEWER)
        assert client.get('/auth/session').json()['role'] == 'admin'
        assert client.get('/learning/audio-review/state').status_code == 200
        assert client.post('/auth/logout', json={}).status_code == 200
        verified_signup(client, mail, 'ordinary@example.com')
        assert client.get('/auth/session').json()['role'] == 'learner'
        assert client.get('/learning/audio-review/state').status_code == 403
        assert client.get('/learning/audio-review/access').status_code == 403
        assert client.put('/account/profile', json={'name': 'New name', 'role': 'admin'}).status_code == 422
        assert client.get('/auth/session').json()['role'] == 'learner'
