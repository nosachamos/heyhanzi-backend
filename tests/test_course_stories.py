"""Story HTTP behavior against a real temporary authored-content file and database."""
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from app.email.providers import FakeEmailProvider
from app.main import create_app
from app.services import course_content, notebook_grammar
from tests.test_auth_flows import browser, verified_signup
from tests.test_course_exercises import BASE, FIRST, MANIFEST, NOTEBOOK


@pytest.fixture
def story_catalog(tmp_path, monkeypatch):
    # The content file is an external boundary. No grading or persistence is mocked.
    item = copy.deepcopy(FIRST)
    item.update(id="EX-STORY-HTTP", activity="ACT-STORY", title="A short story", optional=False)
    paragraphs = []
    steps = []
    for index in range(3):
        paragraph_id = f"paragraph-{index}"
        paragraph = {"id": paragraph_id, "text": "我是人。", "pinyin": "wǒ shì rén", "tokens": []}
        steps.append({"id": f"listen-{paragraph_id}", "kind": "reading", "title": "Read", "next": "Next", "story": {"id": item["id"], "lines": [], "words": [], "context": ""}})
        if index != 1:
            question = {"id": f"question-{index}", "kind": "choice", "prompt": "Who is speaking?", "label": "Who is speaking?", "resultDomain": "Story understanding", "hint": "Look at the speaker.", "choices": [
                {"id": "speaker", "hanzi": "我", "correct": True, "feedback": "The narrator is speaking."},
                {"id": "listener", "hanzi": "你", "correct": False, "feedback": "The listener is not the speaker."}]}
            steps.append({"id": f"check-{paragraph_id}", "kind": "check", "title": "Read", "questions": [question]})
            paragraph["question"] = copy.deepcopy(question)
        paragraphs.append(paragraph)
    item["definition"] = {"id": "ACT-STORY", "instanceId": item["id"], "revision": item["revision"], "title": "Read", "steps": steps,
                          "storyActivity": {"mode": "listen", "sample": {"id": "STORY-HTTP", "paragraphs": paragraphs}}}
    item["fingerprint"] = hashlib.sha256(json.dumps({key: value for key, value in item.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    optional = copy.deepcopy(item)
    optional.update(id="EX-STORY-OPTIONAL", optional=True)
    optional["definition"]["instanceId"] = optional["id"]
    optional["fingerprint"] = hashlib.sha256(json.dumps({key: value for key, value in optional.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # The synthetic story says 我是人 and contrasts 我/你. Its prerequisites
    # follow that authored content, independently of the evolving first cloze.
    story_words = [{'id': wid, 'text': text, 'pinyin': pinyin,
                    'primary_block_ids': ['mandarin-simplified-v1-warmup-' + block]}
                   for wid, text, pinyin, block in [
                       ('W0001', '我', 'wǒ', 'wo'), ('W0002', '你', 'nǐ', 'ni'),
                       ('W0006', '是', 'shì', 'shi'), ('W0027', '人', 'rén', 'ren')]]
    catalog = {**MANIFEST, "exercises": [item, optional, FIRST], "learner_policy": {
        **MANIFEST["learner_policy"], "optional_exercise_ids": [optional["id"], FIRST["id"]],
        "lexical_requirements": {item['id']: story_words, optional['id']: story_words,
                                 FIRST['id']: MANIFEST['learner_policy']['lexical_requirements'][FIRST['id']]},
        "trail_placements": {entry["id"]: MANIFEST["learner_policy"]["trail_placements"][FIRST["id"]] for entry in [item, optional, FIRST]}}}
    # A synthetic authored replacement is not a media-only release of the real
    # course; never carry production aliases into this changed teaching fixture.
    catalog.pop('media_compatibility', None)
    path = tmp_path / "course-exercises.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False))
    monkeypatch.setattr(course_content, "CONTENT", path)
    grammar = {
        'id': 'G-warmup-identity', 'block_id': 'mandarin-simplified-v1-warmup-ren', 'block_sequence': 5,
        'title': {'en': 'Say who it is', 'zh': '说是谁'},
        'explanation': {'en': 'Put 是 between the person and their identity.', 'zh': '用“是”连接人物和身份。'},
        'examples': [{'languageTag': 'cmn-Hans', 'text': '我是人。', 'pinyin': 'wǒ shì rén', 'audio_key': 'grammar-example',
                      'english': 'I am a person.', 'tokens': []}],
        'requires_grammar': [], 'requires_words': ['W0001', 'W0006', 'W0027'],
        'lexical_requirements': [
            {'text': text, 'pinyin': pinyin, 'primary_block_ids': ['mandarin-simplified-v1-warmup-' + block]}
            for text, pinyin, block in [('我', 'wǒ', 'wo'), ('是', 'shì', 'shi'), ('人', 'rén', 'ren')]]}
    follow = copy.deepcopy(grammar)
    follow.update(id='G-http-follow', requires_grammar=[grammar['id']])
    for note in [grammar, follow]:
        note['fingerprint'] = hashlib.sha256(json.dumps(note, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    grammar_path = tmp_path / 'notebook-grammar.json'
    audio = {voice: {'id': f'grammar-{voice}', 'version': 'qualified-1', 'languageTag': 'cmn-Hans',
                    'mimeType': 'audio/mpeg', 'byteLength': 100, 'voiceId': voice,
                    'url': f'https://audio.example.test/grammar-{voice}.mp3'} for voice in ['female', 'male']}
    grammar_path.write_text(json.dumps({'format_version': 1, 'track_id': NOTEBOOK['trackId'],
        'items': [grammar, follow], 'story_requirements': {},
        'audio': {'grammar-example': audio['female'] | {'voices': audio}}}, ensure_ascii=False))
    monkeypatch.setattr(notebook_grammar, 'CONTENT', grammar_path)
    monkeypatch.setitem(NOTEBOOK, 'grammarExposures', {grammar['block_id']: [
        {key: grammar[key] for key in ['id', 'fingerprint']}]})
    return item, optional


@pytest.fixture
def story_app(postgres_config, run_cli, story_catalog):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    return create_app(postgres_config, email_provider=mail), mail, story_catalog


def save(item, draft=None):
    return {"instanceId": item["id"], "revision": item["revision"], "fingerprint": item["fingerprint"], "prepared": True, "draft": draft or [], "supports": []}


def check(item, question, answer="listener"):
    return {"attemptId": str(uuid4()), "revision": item["revision"], "fingerprint": item["fingerprint"], "answer": [question, answer], "supports": []}


def request(client, operation, progress=None, **fields):
    response = client.post(BASE + "/guest", json={"operation": operation, "progress": progress or {"runs": []}, "notebook": NOTEBOOK, **fields})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
def test_notebook_grammar_requires_explicit_current_display_and_preserves_visits(story_app):
    app, mail, (item, _) = story_app
    block = 'mandarin-simplified-v1-warmup-ren'
    with browser(app) as client:
        verified_signup(client, mail, 'grammar-owner@example.com')
        owner = client.get('/auth/session').json()['id']
        assert client.post('/learning/guest/import', json={**NOTEBOOK, 'grammarExposures': {}, 'accountId': owner}).status_code == 200
        state = client.get(BASE).json()
        assert item['id'] not in state['eligible']
        assert state['grammarReviews'][item['id']]['blockId'] == block
        next_id = client.get(f"/learning/tracks/{NOTEBOOK['trackId']}/journey").json()['nextBlockId']
        blocked = client.post(f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{next_id}/complete", json={})
        assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'course_exercise_locked'
        endpoint = f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{block}/vocabulary"
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        notes = response.json()['grammarNotes']
        assert [note['id'] for note in notes] == ['G-warmup-identity', 'G-http-follow']
        assert notes[0]['examples'][0]['text'] == '我是人。'
        assert set(notes[0]['examples'][0]['audio']['voices']) == {'female', 'male'}
        # Fetching or visiting never records an explanation display.
        assert item['id'] not in client.get(BASE).json()['eligible']
        refs = [{key: note[key] for key in ['id', 'fingerprint']} for note in notes]
        assert client.post(endpoint + '/seen', json={'grammar': [refs[1]]}).status_code == 422
        other = endpoint.replace('warmup-ren', 'warmup-wo')
        assert client.post(other + '/seen', json={'grammar': [refs[0]]}).status_code == 422
        assert client.post(endpoint + '/seen', json={'grammar': refs}).status_code == 200
        assert item['id'] in client.get(BASE).json()['eligible']
        assert item['id'] not in client.get(BASE).json()['grammarReviews']
        assert client.get(BASE).json()['completed'] == []  # Teaching is not mastery.
        # A requalified recording does not rewrite the teaching evidence.
        catalog = json.loads(notebook_grammar.CONTENT.read_text())
        catalog['audio']['grammar-example']['version'] = 'qualified-2'
        notebook_grammar.CONTENT.write_text(json.dumps(catalog, ensure_ascii=False))
        replay = client.get(endpoint).json()['grammarNotes'][0]
        assert replay['fingerprint'] == notes[0]['fingerprint']
        assert replay['examples'][0]['audio']['version'] == 'qualified-2'
        assert item['id'] in client.get(BASE).json()['eligible']
        # An authored correction preserves historical rows but requires current teaching.
        catalog = json.loads(notebook_grammar.CONTENT.read_text())
        note = catalog['items'][0]
        note['explanation']['en'] += ' Use the same order for another identity.'
        note['fingerprint'] = hashlib.sha256(json.dumps({key: value for key, value in note.items() if key != 'fingerprint'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        notebook_grammar.CONTENT.write_text(json.dumps(catalog, ensure_ascii=False))
        assert item['id'] not in client.get(BASE).json()['eligible']
        changed = client.post(endpoint + '/seen', json={'grammar': refs})
        assert changed.status_code == 409 and changed.json()['detail']['code'] == 'learning_grammar_changed'
        updated = client.get(endpoint).json()['grammarNotes']
        assert client.post(endpoint + '/seen', json={'grammar': [{key: note[key] for key in ['id', 'fingerprint']} for note in updated]}).status_code == 200
        assert item['id'] in client.get(BASE).json()['eligible']
        journey = client.get(f"/learning/tracks/{NOTEBOOK['trackId']}/journey?level=warmup").json()
        assert journey['completedCount'] == len(NOTEBOOK['completed'])
        assert client.post('/auth/logout', json={}).status_code == 200
        verified_signup(client, mail, 'grammar-other@example.com')
        assert item['id'] not in client.get(BASE).json()['eligible']


@pytest.mark.integration
def test_guest_grammar_rejects_wrong_sources_and_keeps_missing_or_stale_teaching_locked(story_app):
    app, _, (item, _) = story_app
    with browser(app) as client:
        def state(notebook):
            return client.post(BASE + '/guest', json={'operation': 'state', 'notebook': notebook})

        unintroduced = {**NOTEBOOK, 'grammarExposures': {}}
        assert item['id'] not in state(unintroduced).json()['eligible']
        assert state(unintroduced).json()['grammarReviews'][item['id']]['blockId'] == 'mandarin-simplified-v1-warmup-ren'
        next_id = client.post('/learning/guest/lesson', json={**unintroduced, 'operation': 'journey'}).json()['nextBlockId']
        assert client.post(BASE + '/guest', json={'operation': 'advance', 'notebook': unintroduced, 'blockId': next_id}).status_code == 409
        assert item['id'] in state(NOTEBOOK).json()['eligible']
        forged = copy.deepcopy(NOTEBOOK)
        refs = next(iter(forged['grammarExposures'].values()))
        forged['grammarExposures'] = {'mandarin-simplified-v1-warmup-wo': refs}
        assert state(forged).status_code == 422
        stale = copy.deepcopy(NOTEBOOK)
        next(iter(stale['grammarExposures'].values()))[0]['fingerprint'] = '0' * 64
        assert item['id'] not in state(stale).json()['eligible']
        early = {**NOTEBOOK, 'completed': NOTEBOOK['completed'][:2]}
        assert state(early).status_code == 422
        # A next-card explanation can be displayed before that card's visit,
        # but cannot prematurely unlock a story at a later anchor.
        pending = {**NOTEBOOK, 'completed': NOTEBOOK['completed'][:4]}
        response = state(pending)
        assert response.status_code == 200, response.text
        assert item['id'] not in response.json()['eligible']


@pytest.mark.integration
def test_guest_can_reread_a_changed_prerequisite_without_losing_dependent_history(story_app):
    app, mail, (item, _) = story_app
    data = json.loads(notebook_grammar.CONTENT.read_text())
    block = data['items'][0]['block_id']
    progress = copy.deepcopy(NOTEBOOK)
    progress['grammarExposures'] = {block: [{key: note[key] for key in ['id', 'fingerprint']} for note in data['items']]}
    with browser(app) as client:
        def state(value):
            return client.post(BASE + '/guest', json={'operation': 'state', 'notebook': value})

        assert item['id'] in state(progress).json()['eligible']
        parent = data['items'][0]
        parent['explanation']['en'] += ' A corrected explanation.'
        parent['fingerprint'] = hashlib.sha256(json.dumps({key: value for key, value in parent.items() if key != 'fingerprint'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        notebook_grammar.CONTENT.write_text(json.dumps(data, ensure_ascii=False))
        response = state(progress)
        assert response.status_code == 200, response.text
        assert item['id'] not in response.json()['eligible']
        assert response.json()['grammarReviews'][item['id']]['grammarId'] == parent['id']
        missing_parent = copy.deepcopy(progress)
        missing_parent['grammarExposures'][block] = missing_parent['grammarExposures'][block][1:]
        assert state(missing_parent).status_code == 422
        vocabulary = client.post('/learning/guest/lesson', json={**progress, 'operation': 'vocabulary', 'blockId': block})
        assert vocabulary.status_code == 200, vocabulary.text
        # A normal account import also retains recoverability, without crediting
        # the still-current child whose prerequisite has not yet been reread.
        verified_signup(client, mail, 'grammar-revision@example.com')
        owner = client.get('/auth/session').json()['id']
        assert client.post('/learning/guest/import', json={**progress, 'accountId': owner}).status_code == 200
        assert item['id'] not in client.get(BASE).json()['eligible']
        progress['grammarExposures'][block].append({key: parent[key] for key in ['id', 'fingerprint']})
        assert item['id'] in state(progress).json()['eligible']


@pytest.mark.integration
def test_required_story_recovers_skipped_words_before_patterns_for_guests_and_accounts(story_app):
    app, mail, (item, optional) = story_app
    block = 'mandarin-simplified-v1-warmup-hao'
    word = {'id': 'W0007', 'text': '你好', 'pinyin': 'nǐhǎo', 'meaning': 'hello; hi',
            'source_id': 'cmn-32ddf4d4a6f5effadab4f4f8', 'primary_block_ids': [],
            'introduction_block_id': block, 'introduction_block_sequence': 3, 'introduction_level_id': 'warmup'}
    data = json.loads(course_content.CONTENT.read_text())
    for entry in [item, optional]:
        data['learner_policy']['lexical_requirements'][entry['id']].append(word)
    course_content.CONTENT.write_text(json.dumps(data, ensure_ascii=False))
    initial = {**NOTEBOOK, 'exposures': {}, 'grammarExposures': {}}
    with browser(app) as client:
        def guest(operation, progress, **fields):
            return client.post(BASE + '/guest', json={'operation': operation, 'notebook': progress, **fields})

        state = guest('state', initial)
        assert state.status_code == 200, state.text
        assert state.json()['grammarReviews'][item['id']]['wordId'] == word['id']
        assert optional['id'] not in state.json()['grammarReviews']
        assert item['id'] not in state.json()['eligible'] and optional['id'] not in state.json()['eligible']
        next_id = client.post('/learning/guest/lesson', json={**initial, 'operation': 'journey'}).json()['nextBlockId']
        assert guest('advance', initial, blockId=next_id).status_code == 409
        assert guest('exercise', initial, instanceId=item['id']).status_code == 409
        vocabulary = client.post('/learning/guest/lesson', json={**initial, 'operation': 'vocabulary', 'blockId': block})
        assert vocabulary.status_code == 200
        assert any(entry['id'] == word['source_id'] for entry in vocabulary.json()['items'])
        assert item['id'] not in guest('state', initial).json()['eligible']
        with_words = {**initial, 'exposures': {block: [word['source_id']]}}
        review = guest('state', with_words).json()['grammarReviews'][item['id']]
        assert review['grammarId'] == 'G-warmup-identity'
        notes = client.post('/learning/guest/lesson', json={**with_words, 'operation': 'vocabulary', 'blockId': review['blockId']}).json()['grammarNotes']
        with_notes = {**with_words, 'grammarExposures': {review['blockId']: [{key: note[key] for key in ['id', 'fingerprint']} for note in notes]}}
        assert guest('exercise', with_notes, instanceId=item['id']).status_code == 200

        verified_signup(client, mail, 'skipped-words@example.com')
        owner = client.get('/auth/session').json()['id']
        assert client.post('/learning/guest/import', json={**initial, 'accountId': owner}).status_code == 200
        assert client.get(BASE).json()['grammarReviews'][item['id']]['wordId'] == word['id']
        assert client.post(f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{next_id}/complete", json={}).status_code == 409
        endpoint = f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{block}/vocabulary"
        assert client.get(endpoint).status_code == 200
        assert client.post(endpoint + '/seen', json={'vocabularyIds': [word['source_id']]}).status_code == 200
        review = client.get(BASE).json()['grammarReviews'][item['id']]
        assert review['grammarId'] == 'G-warmup-identity'
        endpoint = f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{review['blockId']}/vocabulary"
        notes = client.get(endpoint).json()['grammarNotes']
        assert client.post(endpoint + '/seen', json={'grammar': [{key: note[key] for key in ['id', 'fingerprint']} for note in notes]}).status_code == 200
        assert client.get(BASE + '/exercises/' + item['id']).status_code == 200
        assert client.get(BASE).json()['completed'] == []


@pytest.mark.integration
def test_story_cursor_retry_completion_with_errors_and_independent_optional_branch(story_app):
    app, _, (item, optional) = story_app
    with browser(app) as client:
        # An optional story remains independently skippable/playable.
        assert request(client, "exercise", instanceId=optional["id"])["exercise"]["id"] == optional["id"]
        run_id = str(uuid4())
        response = request(client, "save", runId=run_id, save=save(item, ["check-paragraph-0", "listener"]))
        response = request(client, "check", response["progress"], runId=run_id, check=check(item, "question-0"))
        assert response["run"]["status"] == "retry" and not response["run"]["completed"]
        # Reload preserves the actual selected choice and server feedback.
        restored = request(client, "exercise", response["progress"], instanceId=item["id"])["run"]
        assert restored["draft"] == ["check-paragraph-0", "listener"]
        assert restored["attempts"][0]["correct"] is False
        blocked = client.post(BASE + "/guest", json={"operation": "save", "progress": response["progress"], "notebook": NOTEBOOK, "runId": run_id, "save": save(item, ["check-paragraph-2"])})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "course_story_question_required"
        second_try = check(item, "question-0")
        response = request(client, "check", response["progress"], runId=run_id, check=second_try)
        assert response["run"]["status"] == "draft"
        replay = request(client, "check", response["progress"], runId=run_id, check=second_try)
        assert replay["run"]["attempts"] == response["run"]["attempts"]
        response = request(client, "save", response["progress"], runId=run_id, save=save(item, ["listen-paragraph-1"]))
        assert request(client, "exercise", response["progress"], instanceId=item["id"])["run"]["draft"] == ["listen-paragraph-1"]
        for _ in range(2):
            response = request(client, "check", response["progress"], runId=run_id, check=check(item, "question-2"))
        assert response["run"]["status"] == "completed" and response["run"]["completed"]
        assert all(attempt["correct"] is False for attempt in response["run"]["attempts"])
        state = request(client, "state", response["progress"])
        assert state["completed"] == [item["id"]]
        # A new repeat keeps previous bounded completion evidence.
        repeat = request(client, "save", response["progress"], runId=str(uuid4()), save=save(item))
        assert repeat["run"]["completed"] and repeat["run"]["status"] == "draft"
        assert len(repeat["progress"]["runs"]) == 2


@pytest.mark.integration
def test_story_regrades_guest_claims_and_retains_account_isolation(story_app):
    app, mail, (item, _) = story_app
    with browser(app) as client:
        run_id = str(uuid4())
        response = request(client, "save", runId=run_id, save=save(item))
        invalid = client.post(BASE + "/guest", json={"operation": "check", "progress": response["progress"], "notebook": NOTEBOOK, "runId": run_id, "check": check(item, "question-2")})
        assert invalid.status_code == 422
        response = request(client, "check", response["progress"], runId=run_id, check=check(item, "question-0"))
        forged = copy.deepcopy(response["progress"])
        forged["runs"][0].update(status="completed", completed=True)
        forged["runs"][0]["attempts"][0]["correct"] = True
        normalized = request(client, "state", forged)
        assert normalized["completed"] == [] and normalized["runs"][0]["status"] == "retry"
        verified_signup(client, mail, "story-owner@example.com")
        account = client.get("/auth/session").json()["id"]
        assert client.post("/learning/guest/import", json={**NOTEBOOK, "accountId": account}).status_code == 200
        imported = client.post(BASE + "/import", json={"accountId": account, "progress": normalized["progress"]})
        assert imported.status_code == 200, imported.text
        assert imported.json()["runs"][0]["status"] == "retry"
        response = client.post(f"{BASE}/runs/{run_id}/checks", json=check(item, "question-0", "speaker"))
        assert response.status_code == 200 and response.json()["status"] == "draft"
        response = client.post(f"{BASE}/runs/{run_id}/checks", json=check(item, "question-2", "speaker"))
        assert response.status_code == 200 and response.json()["status"] == "completed"
        assert client.get(BASE).json()["completed"] == [item["id"]]
        assert client.post("/auth/logout", json={}).status_code == 200
        verified_signup(client, mail, "story-other@example.com")
        assert client.get(BASE).json()["completed"] == []
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check(item, "question-2", "speaker")).status_code == 404


@pytest.mark.integration
def test_required_story_gates_new_notebook_visits_for_accounts_and_guest_evidence(story_app):
    app, mail, (item, optional) = story_app
    with browser(app) as client:
        journey = client.post('/learning/guest/lesson', json={**NOTEBOOK, 'operation': 'journey'}).json()
        next_id = journey['nextBlockId']
        blocked = client.post(BASE + '/guest', json={'operation': 'advance', 'notebook': NOTEBOOK,
                                                     'blockId': next_id, 'progress': {'runs': []}})
        assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'course_exercise_locked'
        # Existing notebook cards remain replayable; a new catalogue cannot relock history.
        assert request(client, 'advance', blockId=NOTEBOOK['completed'][-1])['allowed']
        # Before the required anchor, normal progress needs no optional exercise history.
        beginning = client.post('/learning/guest/lesson', json={**NOTEBOOK, 'completed': [], 'grammarExposures': {}, 'operation': 'journey'}).json()
        before_anchor = client.post(BASE + '/guest', json={'operation': 'advance',
            'notebook': {**NOTEBOOK, 'completed': [], 'grammarExposures': {}}, 'blockId': beginning['nextBlockId']})
        assert before_anchor.status_code == 200 and before_anchor.json()['allowed']
        verified_signup(client, mail, 'story-path-owner@example.com')
        owner = client.get('/auth/session').json()['id']
        assert client.post('/learning/guest/import', json={**NOTEBOOK, 'accountId': owner}).status_code == 200
        endpoint = f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{next_id}/complete"
        account_blocked = client.post(endpoint, json={})
        assert account_blocked.status_code == 409 and account_blocked.json()['detail']['code'] == 'course_exercise_locked'
        replay = client.post(f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{NOTEBOOK['completed'][-1]}/complete", json={})
        assert replay.status_code == 200
        run_id = str(uuid4())
        response = request(client, 'save', runId=run_id, save=save(item))
        forged = copy.deepcopy(response['progress'])
        forged['runs'][0].update(completed=True, status='completed')
        forged_advance = client.post(BASE + '/guest', json={'operation': 'advance', 'notebook': NOTEBOOK,
                                                          'blockId': next_id, 'progress': forged})
        assert forged_advance.status_code == 409
        for question in ['question-0', 'question-2']:
            for _ in range(2):
                response = request(client, 'check', response['progress'], runId=run_id, check=check(item, question))
        # A finished attempt, including unresolved answers, unlocks progress;
        # the optional story has never been attempted.
        assert response['run']['completed'] and optional['id'] not in request(client, 'state', response['progress'])['completed']
        assert request(client, 'advance', response['progress'], blockId=next_id)['allowed']
        historical = copy.deepcopy(response['progress'])
        missing_optional = copy.deepcopy(historical['runs'][0])
        missing_optional.update(id=str(uuid4()), instanceId='EX-OLD-OPTIONAL')
        historical['runs'].append(missing_optional)
        assert request(client, 'advance', historical, blockId=next_id)['allowed']
        imported = client.post(BASE + '/import', json={'accountId': owner, 'progress': response['progress']})
        assert imported.status_code == 200, imported.text
        assert client.post(endpoint, json={}).status_code == 200
        assert client.post(endpoint, json={}).status_code == 200
