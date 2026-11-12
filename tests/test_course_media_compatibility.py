"""Media-only releases retain real account/guest answers and resumable drafts."""
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from app.main import create_app
from app.services import course_content
from app.services.course_media import semantic_fingerprint, validate_compatibility
from tests.test_auth_flows import PASSWORD, browser, verified_signup
from tests.test_course_exercises import (
    BASE,
    FIRST,
    MANIFEST,
    SECOND,
    check_payload,
    guest_call,
    guest_pass,
    import_notebook,
    save_payload,
)
from tests.test_course_exercises import (
    course_app as course_app_fixture,
)
from tests.test_course_stories import (
    check as story_check,
)
from tests.test_course_stories import (
    request as story_request,
)
from tests.test_course_stories import (
    save as story_save,
)
from tests.test_course_stories import (
    story_app as story_app_fixture,
)
from tests.test_course_stories import (
    story_catalog as story_catalog_fixture,
)

course_app = course_app_fixture
story_app = story_app_fixture
story_catalog = story_catalog_fixture


def fingerprint(item):
    return hashlib.sha256(json.dumps({k: v for k,v in item.items() if k != 'fingerprint'},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@pytest.fixture
def media_catalog(tmp_path, monkeypatch):
    items = copy.deepcopy([FIRST, SECOND])
    identifiers = {item['id'] for item in items}
    original = MANIFEST['learner_policy']
    policy = {**original,
        'optional_exercise_ids': [value for value in original['optional_exercise_ids'] if value in identifiers],
        'lexical_requirements': {key: value for key,value in original['lexical_requirements'].items() if key in identifiers},
        'trail_placements': {key: value for key,value in original['trail_placements'].items() if key in identifiers}}
    document = {**MANIFEST, 'exercises': items, 'learner_policy': policy}
    document.pop('media_compatibility', None)
    document.pop('definition_storage', None)
    document.pop('indexSha256', None)
    path = tmp_path/'media-course.json'
    path.write_text(json.dumps(document, ensure_ascii=False))
    monkeypatch.setattr(course_content, 'CONTENT', path)
    return path


def promote_media(path):
    document = json.loads(path.read_text())
    aliases = {}
    updated = {}
    for item in document['exercises']:
        previous = copy.deepcopy(item)
        if item['definition']['id'] == 'ACT-CLOZE':
            item['definition']['audio']['replacement-proof'] = {'female': {'url': '/media-new.mp3'}}
        else:
            item['definition']['storyActivity']['recordings'] = {'release': 'replacement-media'}
        item['fingerprint'] = fingerprint(item)
        assert previous['fingerprint'] != item['fingerprint']
        assert semantic_fingerprint(previous) == semantic_fingerprint(item)
        aliases[item['id']] = {'revision': item['revision'], 'currentFingerprint': item['fingerprint'],
            'previousFingerprints': [previous['fingerprint']], 'semanticSha256': semantic_fingerprint(item)}
        updated[item['id']] = item
    document['media_compatibility'] = {'format_version': 1, 'items': aliases,
        'learnerPolicySha256': hashlib.sha256(json.dumps(document.get('learner_policy'),
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    path.write_text(json.dumps(document, ensure_ascii=False))
    return updated


@pytest.mark.integration
def test_account_completion_and_draft_resume_survive_media_release(course_app, media_catalog):
    app, mail = course_app
    first_id, second_id = str(uuid4()), str(uuid4())
    draft = check_payload(SECOND)['answer'][:1]
    with browser(app) as client:
        verified_signup(client, mail, 'media-owner@example.com')
        import_notebook(client)
        assert client.put(f'{BASE}/runs/{first_id}', json=save_payload()).status_code == 200
        assert client.post(f'{BASE}/runs/{first_id}/checks', json=check_payload()).status_code == 200
        assert client.put(f'{BASE}/runs/{second_id}', json=save_payload(SECOND, draft=draft)).status_code == 200
        assert client.get(BASE).json()['completed'] == [FIRST['id']]
        updated = promote_media(media_catalog)
        state = client.get(BASE).json()
        assert state['completed'] == [FIRST['id']]
        first = client.get(f'{BASE}/exercises/{FIRST["id"]}').json()['run']
        second = client.get(f'{BASE}/exercises/{SECOND["id"]}').json()['run']
        assert first['id'] == first_id and first['completed'] and not first['archived']
        assert first['fingerprint'] == updated[FIRST['id']]['fingerprint']
        assert second['id'] == second_id and second['draft'] == draft
        assert second['fingerprint'] == updated[SECOND['id']]['fingerprint']
        owner = client.get('/auth/session').json()['id']
        imported = client.post(BASE+'/import', json={'accountId': owner, 'progress': {'runs': [first]}})
        assert imported.status_code == 200, imported.text
        assert client.post(f'{BASE}/runs/{second_id}/checks', json=check_payload(updated[SECOND['id']])).status_code == 200
    replacement = create_app(app.state.config, email_provider=mail)
    with browser(replacement) as client:
        login = client.post('/auth/email/login', json={'email': 'media-owner@example.com', 'password': PASSWORD})
        assert login.status_code == 200
        assert client.get(BASE).json()['completed'] == [FIRST['id'], SECOND['id']]


@pytest.mark.integration
def test_guest_completion_draft_and_import_use_current_fingerprint(course_app, media_catalog):
    app, mail = course_app
    with browser(app) as client:
        passed = guest_pass(client)
        second_id = str(uuid4())
        draft = check_payload(SECOND)['answer'][:1]
        progress = guest_call(client, 'save', passed['progress'], runId=second_id,
                              save=save_payload(SECOND, draft=draft))['progress']
        updated = promote_media(media_catalog)
        restored = guest_call(client, 'state', progress)
        assert restored['completed'] == [FIRST['id']]
        assert all(run['fingerprint'] == updated[run['instanceId']]['fingerprint']
                   and not run['archived'] for run in restored['progress']['runs'])
        resumed = guest_call(client, 'exercise', restored['progress'], instanceId=SECOND['id'])['run']
        assert resumed['id'] == second_id and resumed['draft'] == draft
        # Old in-flight clients can finish the compatible run too.
        finished = guest_call(client, 'check', progress, runId=second_id, check=check_payload(SECOND))
        assert finished['run']['fingerprint'] == updated[SECOND['id']]['fingerprint']
        assert guest_call(client, 'state', finished['progress'])['completed'] == [FIRST['id'], SECOND['id']]
        forged = {'runs': copy.deepcopy(progress['runs'])}
        forged['runs'][0]['fingerprint'] = '0' * 64
        rejected = guest_call(client, 'state', forged)
        assert rejected['completed'] == []
        assert rejected['progress']['runs'][0]['archived']
        assert rejected['progress']['runs'][0]['attempts'][0]['correct'] is None
        verified_signup(client, mail, 'media-guest-owner@example.com')
        import_notebook(client)
        account_id = client.get('/auth/session').json()['id']
        imported = client.post(BASE+'/import', json={'accountId': account_id, 'progress': finished['progress']})
        assert imported.status_code == 200, imported.text
        assert client.get(BASE).json()['completed'] == [FIRST['id'], SECOND['id']]


@pytest.mark.integration
def test_required_story_account_and_guest_completion_survive_media_release(story_app):
    app, mail, (item, optional) = story_app
    with browser(app) as client:
        progress = None
        run_id = str(uuid4())
        saved = story_request(client, 'save', progress, runId=run_id, save=story_save(item))
        for step in [step for step in item['definition']['steps'] if step['kind'] == 'check']:
            saved = story_request(client, 'check', saved['progress'], runId=run_id,
                check=story_check(item, step['questions'][0]['id'], 'speaker'))
        progress = saved['progress']
        assert item['id'] in story_request(client, 'state', progress)['completed']
        verified_signup(client, mail, 'media-story-owner@example.com')
        import_notebook(client)
        owner = client.get('/auth/session').json()['id']
        assert client.post(BASE+'/import', json={'accountId': owner, 'progress': progress}).status_code == 200
        updated = promote_media(course_content.CONTENT)
        assert item['id'] in client.get(BASE).json()['completed']
        detail = client.get(f'{BASE}/exercises/{item["id"]}').json()
        assert detail['run']['id'] == run_id and detail['run']['fingerprint'] == updated[item['id']]['fingerprint']
        guest = story_request(client, 'state', progress)
        assert item['id'] in guest['completed']
        assert guest['progress']['runs'][0]['fingerprint'] == updated[item['id']]['fingerprint']
        assert story_request(client, 'exercise', guest['progress'], instanceId=optional['id'])['exercise']['id'] == optional['id']


@pytest.mark.parametrize('change', ['text', 'answer', 'preparation', 'revision', 'unknown-id', 'unknown-alias', 'root-prerequisite'])
def test_compatibility_metadata_rejects_changed_teaching_or_unknown_identity(change):
    item = copy.deepcopy(FIRST)
    item['definition']['audio'] = {'new-media': {}}
    item['fingerprint'] = fingerprint(item)
    row = {'revision': item['revision'], 'currentFingerprint': item['fingerprint'],
        'previousFingerprints': [FIRST['fingerprint']], 'semanticSha256': semantic_fingerprint(item)}
    policy = {'trail_placements': {item['id']: {'afterSequence': 1}}}
    policy_sha = hashlib.sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    identifier = item['id']
    if change == 'text':
        item['definition']['steps'][0]['questions'][0]['prompt'] += ' Changed meaning.'
    elif change == 'answer':
        item['definition']['steps'][0]['questions'][0]['solutions'] = []
    elif change == 'preparation':
        item['preparation']['grammar']['hint'] += ' New prerequisite.'
    elif change == 'revision':
        row['revision'] += 1
    elif change == 'unknown-id':
        identifier = 'EX-UNKNOWN'
    elif change == 'unknown-alias':
        row['previousFingerprints'] = [item['fingerprint']]
    else:
        policy['trail_placements'][item['id']]['afterSequence'] = 2
    if change in {'text', 'answer', 'preparation'}:
        item['fingerprint'] = fingerprint(item)
        row['currentFingerprint'] = item['fingerprint']
    with pytest.raises(ValueError):
        validate_compatibility({'learner_policy': policy, 'media_compatibility': {
            'format_version': 1, 'learnerPolicySha256': policy_sha, 'items': {identifier: row}}}, {item['id']: item})
