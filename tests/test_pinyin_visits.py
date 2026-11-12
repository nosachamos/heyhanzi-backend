"""Bounded public-API check for pinyin reading history and account isolation."""
import pytest

from app.email.providers import FakeEmailProvider
from app.main import create_app
from tests.test_auth_flows import PASSWORD, browser, verified_signup

pytestmark = pytest.mark.integration
TRACK = 'mandarin-simplified-v1'
BASE = '/learning/tracks/' + TRACK


def test_pinyin_visits_survive_login_and_guest_import_without_word_credit(postgres_config, run_cli):
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    app = create_app(postgres_config, email_provider=mail)
    with browser(app) as client:
        verified_signup(client, mail)
        assert client.post("/learning/enrollments", json={"trackId": TRACK}).status_code == 200
        initial = client.get(BASE + '/journey').json()
        assert initial['pinyinCompleted'] == [] and initial['completedCount'] == 0
        assert client.post(BASE + '/pinyin/tones/visit', json={}).status_code == 409
        assert client.post(BASE + '/pinyin/unknown/visit', json={}).status_code == 404
        for _ in range(2):
            response = client.post(BASE + '/pinyin/welcome/visit', json={})
            assert response.status_code == 200, response.text
            assert response.json()['pinyinCompleted'] == ['welcome']
        payload = {'trackId': TRACK, 'completed': [], 'exposures': {}, 'pinyinCompleted': ['welcome', 'sounds']}
        guest = client.post('/learning/guest/lesson', json=payload)
        assert guest.status_code == 200, guest.text
        assert guest.json()['pinyinCompleted'] == ['welcome', 'sounds']
        assert guest.json()['completedCount'] == 0
        bad = client.post('/learning/guest/lesson', json={**payload, 'pinyinCompleted': ['tones']})
        assert bad.status_code == 422
        account = client.get('/auth/session').json()['id']
        imported = client.post('/learning/guest/import', json={**payload, 'accountId': account})
        assert imported.status_code == 200, imported.text
        for section in ['tones', 'spelling']:
            assert client.post(BASE + f'/pinyin/{section}/visit', json={}).status_code == 200
        current = client.get(BASE + '/journey').json()
        assert current['completedCount'] == 0 and current['nextBlockId'] == initial['nextBlockId']
        assert current['pinyinCompleted'] == ['welcome', 'sounds', 'tones', 'spelling']
        assert client.post('/auth/logout', json={}).status_code == 200
        assert client.post(BASE + '/pinyin/welcome/visit', json={}).status_code == 401
    with browser(app) as second:
        verified_signup(second, mail, 'pinyin-other@example.com')
        assert second.post('/learning/enrollments', json={'trackId': TRACK}).status_code == 200
        assert second.get(BASE + '/journey').json()['pinyinCompleted'] == []
    with browser(app) as returning:
        login = returning.post('/auth/email/login', json={'email': 'learner@example.com', 'password': PASSWORD})
        assert login.status_code == 200, login.text
        response = returning.get(BASE + '/journey')
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.json()['pinyinCompleted'] == ['welcome', 'sounds', 'tones', 'spelling']
    # Re-running the migration leaves the recorded reading visits intact.
    result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', 'head')
    assert result.returncode == 0, result.stderr
    result = run_cli(postgres_config, '-m', 'alembic', 'check')
    assert result.returncode == 0, result.stderr
