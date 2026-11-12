"""Exercise public course APIs, real grading and additive database migration."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from app.email.providers import FakeEmailProvider
from app.main import create_app
from app.services.course_content import exercise as authored_exercise
from tests.notebook_history import complete_display_history
from tests.test_auth_flows import browser, verified_signup

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "app/data/learning/course-exercises.json").read_text())
FIRST, SECOND = [authored_exercise(item["id"]) for item in MANIFEST["exercises"] if item["definition"]["id"] == "ACT-CLOZE"][:2]
BASE = "/learning/course"
WARMUP = json.loads((Path(__file__).resolve().parents[1] / "alembic/data/0004_warmup.json").read_text())
CURRENT_ORDER = json.loads((Path(__file__).resolve().parents[1] / "alembic/data/0036_notebook_order.json").read_text())
NOTEBOOK = {"trackId": "mandarin-simplified-v1", "completed": [row["blockId"] for row in CURRENT_ORDER["cards"] if row["levelId"] == "warmup"], "exposures": {}}


def import_notebook(client, notebook=None):
    account = client.get("/auth/session").json()["id"]
    response = client.post("/learning/guest/import", json={**(notebook or NOTEBOOK), "accountId": account})
    assert response.status_code == 200, response.text


def notebook_at(config, instance_id, *, later=0):
    """Actual pinned notebook visits plus explicit historical vocabulary displays."""
    policy = MANIFEST["learner_policy"]
    anchor = policy["trail_placements"][instance_id]
    with psycopg2.connect(config["DATABASE"]["Url"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT b.id FROM learning_word_blocks b JOIN learning_stages s ON s.id=b.stage_id "
                           "WHERE s.track_id=%s AND b.kind='word' AND b.sequence<=%s ORDER BY b.sequence",
                           (NOTEBOOK["trackId"], anchor["afterSequence"] + later))
            completed = [row[0] for row in cursor.fetchall()]
    notebook = {"trackId": NOTEBOOK["trackId"], "completed": completed, "exposures": {}}
    return complete_display_history(config, notebook, instance_id)


def remove_display(notebook, source_id):
    for block in notebook['exposures']:
        notebook['exposures'][block] = [id for id in notebook['exposures'][block] if id != source_id]
    for block in notebook.get('lexicalUseExposures', {}):
        notebook['lexicalUseExposures'][block] = [ref for ref in notebook['lexicalUseExposures'][block] if ref['id'] != source_id]


def save_payload(item=FIRST, **changes):
    return {"instanceId": item["id"], "revision": item["revision"], "fingerprint": item["fingerprint"],
            "prepared": True, "draft": [], "supports": [], **changes}


def check_payload(item=FIRST, *, correct=True, **changes):
    if "steps" not in item["definition"]:
        item = authored_exercise(item["id"])
    return {"attemptId": str(uuid4()), "revision": item["revision"], "fingerprint": item["fingerprint"],
            "answer": item["definition"]["steps"][0]["questions"][0]["solutions"][0] if correct else [],
            "supports": [], **changes}


def guest_call(client, operation, progress=None, **fields):
    response = client.post(BASE + "/guest", json={"operation": operation, "progress": progress or {"runs": []}, "notebook": NOTEBOOK, **fields})
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def guest_pass(client, progress=None, item=FIRST):
    run_id = str(uuid4())
    saved = guest_call(client, "save", progress, runId=run_id, save=save_payload(item))
    return guest_call(client, "check", saved["progress"], runId=run_id, check=check_payload(item))


def display_fixture_common_words(client):
    """Display required lexical readings on their real earlier notebook cards."""
    requirements = {}
    for item in [FIRST, SECOND]:
        for word in MANIFEST['learner_policy']['lexical_requirements'][item['id']]:
            if not word['primary_block_ids'] and word.get('source_id'):
                requirements.setdefault(word['introduction_block_id'], {})[word['source_id']] = word
    notebook = {**NOTEBOOK, 'exposures': {}, 'grammarExposures': {}, 'lexicalUseExposures': {}}
    remaining = {key: value for by_source in requirements.values() for key, value in by_source.items()}
    for position, block_id in enumerate(NOTEBOOK['completed']):
        cursor = None
        while True:
            response = client.post('/learning/guest/lesson', json={
                **notebook, 'completed': NOTEBOOK['completed'][:position + 1],
                'operation': 'vocabulary', 'blockId': block_id, 'vocabularyAfter': cursor})
            assert response.status_code == 200, response.text
            page = response.json()
            for word in page['items']:
                requirement = remaining.pop(word['id'], None)
                if requirement:
                    assert word['text'] == requirement['text']
                    assert word['pronunciation']['value'].replace(' ', '') == requirement['pinyin'].replace(' ', '')
                notebook['exposures'].setdefault(block_id, []).append(word['id'])
                if word.get('lexicalUseReference'):
                    notebook['lexicalUseExposures'].setdefault(block_id, []).append(word['lexicalUseReference'])
            if page.get('grammarNotes'):
                notebook['grammarExposures'][block_id] = [
                    {key: note[key] for key in ['id', 'fingerprint']} for note in page['grammarNotes']]
            cursor = page['nextCursor']
            if not cursor:
                break
    assert not remaining, f'Required Common words were not displayed: {list(remaining)}'
    return notebook


@pytest.mark.integration
def test_guest_preparation_drafts_grading_retry_and_idempotency(course_app):
    with browser(course_app[0]) as client:
        assert client.get(BASE).status_code == 401
        exercise = guest_call(client, "exercise", instanceId=FIRST["id"])
        assert exercise["exercise"]["fingerprint"] == FIRST["fingerprint"]
        run_id = str(uuid4())
        saved = guest_call(client, "save", runId=run_id, save=save_payload(prepared=False))
        request = {"notebook": NOTEBOOK, "operation": "check", "progress": saved["progress"], "runId": run_id, "check": check_payload()}
        assert client.post(BASE + "/guest", json=request).json()["detail"]["code"] == "course_preparation_required"
        saved = guest_call(client, "save", saved["progress"], runId=run_id, save=save_payload())
        assert saved["run"]["prepared"] and saved["run"]["supports"] == []
        legacy = guest_call(client, "save", runId=str(uuid4()), save=save_payload(supports=["preparation"]))
        assert legacy["run"]["supports"] == ["preparation"]
        wrong = check_payload(correct=False)
        retry = guest_call(client, "check", saved["progress"], runId=run_id, check=wrong)
        assert retry["run"]["status"] == "retry" and not retry["run"]["completed"]
        replay = guest_call(client, "check", retry["progress"], runId=run_id, check=wrong)
        assert replay["run"]["attempts"] == retry["run"]["attempts"]
        conflict = {"notebook": NOTEBOOK, "operation": "check", "progress": retry["progress"], "runId": run_id,
                    "check": {**wrong, "answer": check_payload()["answer"]}}
        assert client.post(BASE + "/guest", json=conflict).status_code == 409
        passed = guest_call(client, "check", retry["progress"], runId=run_id, check=check_payload())
        assert passed["run"]["status"] == "passed" and passed["run"]["completed"]
        assert len(passed["run"]["attempts"]) == 2
        assert guest_call(client, "state", passed["progress"])["completed"] == [FIRST["id"]]
        assert guest_call(client, "exercise", passed["progress"], instanceId=SECOND["id"])["exercise"]["id"] == SECOND["id"]
        request["progress"] = passed["progress"]
        assert client.post(BASE + "/guest", json=request).status_code == 409


@pytest.mark.integration
def test_guest_cannot_forge_outcomes_or_use_stale_content(course_app):
    with browser(course_app[0]) as client:
        stale = client.post(BASE + "/guest", json={"notebook": NOTEBOOK, "operation": "save", "runId": str(uuid4()), "save": save_payload(fingerprint="0" * 64)})
        assert stale.status_code == 409
        run_id = str(uuid4())
        saved = guest_call(client, "save", runId=run_id, save=save_payload())
        wrong = guest_call(client, "check", saved["progress"], runId=run_id, check=check_payload(correct=False))
        forged = copy.deepcopy(wrong["progress"])
        forged["runs"][0].update(status="passed", completed=True)
        forged["runs"][0]["attempts"][0].update(correct=True, feedback="Forged success")
        normalized = guest_call(client, "state", forged)
        assert normalized["completed"] == [] and normalized["runs"][0]["status"] == "retry"
        detail = guest_call(client, "exercise", normalized["progress"], instanceId=FIRST["id"])["run"]
        assert detail["attempts"][0]["correct"] is False
        invalid_tiles = check_payload(answer=[check_payload()["answer"][0]] * 2)
        assert client.post(BASE + "/guest", json={"notebook": NOTEBOOK, "operation": "check", "progress": wrong["progress"], "runId": run_id, "check": invalid_tiles}).status_code == 422


@pytest.mark.integration
def test_guest_optional_practice_can_skip_earlier_exercises_without_losing_their_drafts(course_app):
    with browser(course_app[0]) as client:
        earlier_id = str(uuid4())
        draft = check_payload()["answer"][:1]
        saved = guest_call(client, "save", runId=earlier_id, save=save_payload(draft=draft))
        later = guest_pass(client, saved["progress"], SECOND)
        state = guest_call(client, "state", later["progress"])
        assert state["completed"] == [SECOND["id"]]
        earlier = guest_call(client, "exercise", state["progress"], instanceId=FIRST["id"])["run"]
        assert earlier["id"] == earlier_id and earlier["draft"] == draft
        assert earlier["fingerprint"] == FIRST["fingerprint"] and not earlier["archived"]
        assert earlier["status"] == "draft" and not earlier["completed"]
        assert SECOND["id"] in state["eligible"]
        assert MANIFEST["exercises"][-1]["id"] not in state["eligible"]


@pytest.mark.integration
def test_guest_compaction_preserves_last_pass_and_latest_repeat(course_app):
    with browser(course_app[0]) as client:
        progress = guest_pass(client)["progress"]
        for _ in range(4):
            run_id = str(uuid4())
            progress = guest_call(client, "save", progress, runId=run_id, save=save_payload())["progress"]
            for _ in range(2):
                response = guest_call(client, "check", progress, runId=run_id, check=check_payload(correct=False))
                progress = response["progress"]
            assert response["run"]["status"] == "failed" and response["run"]["completed"]
            assert len(progress["runs"]) == 2
        state = guest_call(client, "state", progress)
        assert state["completed"] == [FIRST["id"]] and state["runs"][0]["status"] == "failed"
        assert state["runs"][0]["completed"] is True


def test_guest_preserves_full_course_history_without_inventing_notebook_exposure(app_config):
    # A restored guest document contains answers, not authority to award passes.
    # The public API must regrade this complete serialized course prefix.
    runs = []
    retained = [item for item in MANIFEST["exercises"] if item["definition"]["id"] in {"ACT-CLOZE", "ACT-STORY", "ACT-STORY-COMP"}][:-1]
    for indexed in retained:
        item = authored_exercise(indexed["id"])
        if item["definition"].get("storyActivity"):
            steps = [step for step in item["definition"]["steps"] if step["kind"] == "check"]
            attempts = []
            for step in steps:
                question = step["questions"][0]
                choice = next(choice for choice in question["choices"] if choice["correct"])
                attempts.append({"id": str(uuid4()), "answer": [question["id"], choice["id"]], "supports": []})
            draft = [steps[-1]["id"], attempts[-1]["answer"][1]]
        else:
            draft = check_payload(item)["answer"]
            attempts = [{"id": str(uuid4()), "answer": draft, "supports": []}]
        runs.append({**save_payload(item, draft=draft), "id": str(uuid4()), "attempts": attempts})
    with browser(create_app(app_config)) as client:
        restored = guest_call(client, "state", {"runs": runs}, notebook=None)
        assert len(restored["completed"]) == len(retained)
        assert restored["eligible"] == []
        blocked = client.post(BASE + "/guest", json={"operation": "exercise", "instanceId": FIRST["id"], "progress": restored["progress"]})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "course_vocabulary_not_introduced"


@pytest.mark.integration
def test_guest_archives_preserve_history_without_blocking_current_practice_or_unlocking(course_app):
    with browser(course_app[0]) as client:
        first = guest_pass(client)
        second = guest_pass(client, first["progress"], SECOND)
        # Exercise legacy full-answer history without a separate valid signed pass receipt.
        old_progress = {"runs": copy.deepcopy(second["progress"]["runs"])}
        old_progress["runs"][0]["fingerprint"] = "0" * 64
        normalized = guest_call(client, "state", old_progress)
        assert normalized["completed"] == [SECOND["id"]]
        assert len(normalized["runs"]) == 1 and normalized["runs"][0]["instanceId"] == SECOND["id"]
        assert normalized["runs"][0]["completed"]
        archived = normalized["progress"]["runs"][0]
        assert archived["archived"] and not archived["completed"]
        assert archived["attempts"][0]["correct"] is None and archived["attempts"][0]["feedback"] is None
        assert guest_call(client, "exercise", normalized["progress"], instanceId=FIRST["id"])["run"] is None
        assert guest_call(client, "exercise", normalized["progress"], instanceId=SECOND["id"])["run"]["completed"]
        assert client.post(BASE + "/guest", json={"notebook": NOTEBOOK, "operation": "check", "progress": normalized["progress"],
            "runId": archived["id"], "check": check_payload(fingerprint="0" * 64)}).status_code == 409
        # A server-regraded optional answer counts only for that task, without
        # manufacturing completion of an earlier archived or skipped task.
        only_second = guest_call(client, "state", {"runs": [old_progress["runs"][1]]})
        assert only_second["completed"] == [SECOND["id"]] and only_second["runs"][0]["completed"]
        new_id = str(uuid4())
        saved = guest_call(client, "save", normalized["progress"], runId=new_id, save=save_payload())
        assert len(saved["progress"]["runs"]) == 3  # Old first, current first, current second.
        restored = guest_call(client, "check", saved["progress"], runId=new_id, check=check_payload())
        assert guest_call(client, "state", restored["progress"])["completed"] == [FIRST["id"], SECOND["id"]]
        assert len(restored["progress"]["runs"]) == 2


@pytest.mark.integration
def test_guest_compaction_keeps_current_draft_when_archive_is_last(course_app):
    with browser(course_app[0]) as client:
        first = guest_pass(client)
        current_id = str(uuid4())
        draft = check_payload(SECOND)["answer"][:1]
        second = guest_call(client, "save", first["progress"], runId=current_id,
                            save=save_payload(SECOND, draft=draft))
        archived = {**second["run"], "id": str(uuid4()), "fingerprint": "0" * 64}
        progress = {"runs": [*second["progress"]["runs"], archived]}
        # Saving an unrelated repeat must not drop the actionable second draft.
        saved = guest_call(client, "save", progress, runId=str(uuid4()), save=save_payload())
        detail = guest_call(client, "exercise", saved["progress"], instanceId=SECOND["id"])
        assert detail["run"]["id"] == current_id and detail["run"]["draft"] == draft
        assert {run["id"] for run in saved["progress"]["runs"] if run["instanceId"] == SECOND["id"]} == {current_id, archived["id"]}


@pytest.fixture
def course_app(postgres_config, run_cli, monkeypatch):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    app = create_app(postgres_config, email_provider=mail)
    with browser(app) as client:
        for key, value in display_fixture_common_words(client).items():
            monkeypatch.setitem(NOTEBOOK, key, value)
    return app, mail


@pytest.mark.integration
def test_authenticated_draft_reload_grading_and_account_isolation(course_app):
    app, mail = course_app
    with browser(app) as client:
        verified_signup(client, mail, "course-owner@example.com")
        import_notebook(client)
        run_id = str(uuid4())
        draft = check_payload()["answer"][:2]
        saved = client.put(f"{BASE}/runs/{run_id}", json=save_payload(draft=draft))
        assert saved.status_code == 200, saved.text
        assert client.get(f"{BASE}/exercises/{FIRST['id']}").json()["run"]["draft"] == draft
        assert client.get(BASE).json()["runs"][0]["draft"] == draft
        assert client.put(f"{BASE}/runs/{run_id}", json=save_payload(draft=draft)).json() == saved.json()
        later_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{later_id}", json=save_payload(SECOND)).status_code == 200
        assert client.post(f"{BASE}/runs/{later_id}/checks", json=check_payload(SECOND)).json()["completed"]
        assert client.get(BASE).json()["completed"] == [SECOND["id"]]
        earlier = client.get(f"{BASE}/exercises/{FIRST['id']}").json()["run"]
        assert earlier["id"] == run_id and earlier["draft"] == draft and not earlier["archived"]
        passed = client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload())
        assert passed.status_code == 200 and passed.json()["completed"]
        assert client.get(BASE).json()["completed"] == [FIRST["id"], SECOND["id"]]
        assert client.get(f"{BASE}/exercises/{SECOND['id']}").status_code == 200
        assert client.post(f"/learning/tracks/mandarin-simplified-v1/blocks/{FIRST['id']}/complete", json={}).status_code == 404
        assert client.post("/auth/logout", json={}).status_code == 200
        verified_signup(client, mail, "course-other@example.com")
        assert client.get(BASE).json()["completed"] == []
        assert client.get(BASE).json()["runs"] == []
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload()).status_code == 404
        assert client.get(BASE).json()["eligible"] == []
        assert client.get(f"{BASE}/exercises/{SECOND['id']}").status_code == 409
        import_notebook(client)
        assert client.get(f"{BASE}/exercises/{SECOND['id']}").json()["run"] is None


@pytest.mark.integration
def test_authenticated_parallel_checks_are_idempotent_and_enforce_two_attempts(course_app):
    app, mail = course_app
    with browser(app) as client:
        verified_signup(client, mail, "course-retry@example.com")
        import_notebook(client)
        run_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{run_id}", json=save_payload()).status_code == 200
        wrong = check_payload(correct=False)
        with ThreadPoolExecutor(max_workers=2) as workers:
            responses = list(workers.map(lambda _: client.post(f"{BASE}/runs/{run_id}/checks", json=wrong), range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert all(len(response.json()["attempts"]) == 1 for response in responses)
        final = client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload(correct=False))
        assert final.status_code == 200 and final.json()["status"] == "failed"
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload()).status_code == 409
        assert client.get(BASE).json()["completed"] == []
        later_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{later_id}", json=save_payload()).status_code == 200
        assert client.post(f"{BASE}/runs/{later_id}/checks", json=check_payload()).json()["completed"]


@pytest.mark.integration
def test_guest_import_regrades_is_account_bound_and_atomic(course_app):
    app, mail = course_app
    with browser(app) as client:
        guest = guest_pass(client)
        verified_signup(client, mail, "course-import@example.com")
        account = client.get("/auth/session").json()["id"]
        payload = {"accountId": account, "progress": guest["progress"]}
        assert client.post(BASE + "/import", json={**payload, "accountId": "another-user"}).status_code == 409
        assert client.get(BASE).json()["completed"] == []
        imported = client.post(BASE + "/import", json=payload)
        assert imported.status_code == 200, imported.text
        assert imported.json()["completed"] == [FIRST["id"]]
        assert client.post(BASE + "/import", json=payload).json()["completed"] == [FIRST["id"]]
        assert len(client.get(BASE).json()["runs"]) == 1
        progressed = guest_pass(client, guest["progress"], SECOND)
        conflict = copy.deepcopy(progressed["progress"])
        conflict["runs"][0]["attempts"][0]["id"] = str(uuid4())
        assert client.post(BASE + "/import", json={"accountId": account, "progress": conflict}).status_code == 409
        assert client.get(BASE).json()["completed"] == [FIRST["id"]]
        assert client.post(BASE + "/import", json={"accountId": account, "progress": progressed["progress"]}).json()["completed"] == [FIRST["id"], SECOND["id"]]


@pytest.mark.integration
def test_course_migration_preserves_legacy_notebooks_and_progress(postgres_config, run_cli):
    result = run_cli(postgres_config, "-m", "alembic", "upgrade", "0018_all_character_strokes")
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    with browser(create_app(postgres_config, email_provider=mail)) as client:
        verified_signup(client, mail, "course-migration@example.com")
        path_url = "/learning/tracks/mandarin-simplified-v1/path"
        # Historical fixtures use the schema that existed before the course
        # migration; the current ORM also requires later pinyin columns.
        user_id = client.get('/auth/session').json()['id']
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO learning_enrollments (id,user_id,track_id,created_at) VALUES (%s,%s,%s,1)",
                               ('course-legacy-enroll', user_id, 'mandarin-simplified-v1'))
                cursor.execute("SELECT b.id FROM learning_word_blocks b JOIN learning_stages s ON s.id=b.stage_id WHERE s.track_id=%s AND s.position=1 ORDER BY b.position LIMIT 4", ('mandarin-simplified-v1',))
                first_ids = [row[0] for row in cursor.fetchall()]
                for block_id in first_ids[:3]:
                    cursor.execute("INSERT INTO learning_word_completions (enrollment_id,block_id,completed_at) VALUES (%s,%s,123)",
                                   ('course-legacy-enroll', block_id))
                cursor.execute('SELECT row_to_json(b) FROM learning_word_blocks b ORDER BY id')
                stored_blocks = cursor.fetchall()
                cursor.execute('SELECT * FROM learning_word_completions ORDER BY block_id')
                stored_completions = cursor.fetchall()
        for _ in range(2):
            result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', '0019_course_exercise_runs')
            assert result.returncode == 0, result.stderr
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT row_to_json(b) FROM learning_word_blocks b ORDER BY id')
                assert cursor.fetchall() == stored_blocks
                cursor.execute('SELECT * FROM learning_word_completions ORDER BY block_id')
                assert cursor.fetchall() == stored_completions
                cursor.execute('SELECT count(*) FROM learning_course_exercise_runs')
                assert cursor.fetchone() == (0,)
        result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', 'head')
        assert result.returncode == 0, result.stderr
        before = client.get(path_url).json()
        assert before['completedCount'] == 3
        current_words = [b for stage in before['stages'] for b in stage['blocks'] if b['kind'] == 'word']
        assert before['nextBlockId'] == next(b['id'] for b in current_words if b['id'] not in first_ids[:3])
        assert client.get(BASE).json()["completed"] == []
        run_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{run_id}", json=save_payload()).status_code == 409
        import_notebook(client, {**NOTEBOOK, **display_fixture_common_words(client)})
        introduced = client.get(path_url).json()
        assert client.put(f"{BASE}/runs/{run_id}", json=save_payload()).status_code == 200
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload()).json()["completed"]
        assert client.get(path_url).json() == introduced


@pytest.mark.integration
def test_historical_pass_is_retained_without_claiming_current_content_completion(course_app, postgres_config):
    app, mail = course_app
    with browser(app) as client:
        verified_signup(client, mail, "course-old-content@example.com")
        import_notebook(client)
        run_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{run_id}", json=save_payload()).status_code == 200
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload()).json()["completed"]
        # Seed a prior-content snapshot in this disposable DB, without modifying
        # the shared authored manifest or any running development server.
        with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE learning_course_exercise_runs SET fingerprint = %s WHERE id = %s", ("0" * 64, run_id))
        state = client.get(BASE).json()
        assert state["completed"] == []
        assert state["runs"] == []
        detail = client.get(f"{BASE}/exercises/{FIRST['id']}").json()
        assert detail["run"] is None
        with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT passed FROM learning_course_exercise_runs WHERE id = %s", (run_id,))
                assert cursor.fetchone() == (True,)
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload()).status_code == 409
        assert client.get(BASE).json()["completed"] == []
        run_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{run_id}", json=save_payload()).status_code == 200
        assert client.post(f"{BASE}/runs/{run_id}/checks", json=check_payload()).json()["completed"]
        assert client.get(BASE).json()["completed"] == [FIRST["id"]]


@pytest.mark.integration
def test_archival_guest_import_retains_unassessed_history_and_independent_current_passes(course_app, postgres_config):
    app, mail = course_app
    with browser(app) as client:
        first = guest_pass(client)
        second = guest_pass(client, first["progress"], SECOND)
        progress = copy.deepcopy(second["progress"])
        progress["runs"][0]["fingerprint"] = "0" * 64
        archived_id = progress["runs"][0]["id"]
        verified_signup(client, mail, "course-archive-import@example.com")
        import_notebook(client)
        account = client.get("/auth/session").json()["id"]
        payload = {"accountId": account, "progress": progress}
        imported = client.post(BASE + "/import", json=payload)
        assert imported.status_code == 200, imported.text
        assert imported.json()["completed"] == [SECOND["id"]]
        assert len(imported.json()["runs"]) == 1 and imported.json()["runs"][0]["completed"]
        assert client.post(BASE + "/import", json=payload).status_code == 200
        with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT passed, attempts FROM learning_course_exercise_runs WHERE id = %s", (archived_id,))
                passed, attempts = cursor.fetchone()
                assert not passed and attempts[0]["correct"] is None
                # A previously server-graded historical row must never be replaced
                # by an unassessed guest archive with the same identity.
                cursor.execute("UPDATE learning_course_exercise_runs SET passed = true WHERE id = %s", (archived_id,))
        assert client.post(BASE + "/import", json=payload).status_code == 200
        with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT passed FROM learning_course_exercise_runs WHERE id = %s", (archived_id,))
                assert cursor.fetchone() == (True,)
        assert client.get(BASE).json()["completed"] == [SECOND["id"]]
        current_id = str(uuid4())
        assert client.put(f"{BASE}/runs/{current_id}", json=save_payload()).status_code == 200
        assert client.post(f"{BASE}/runs/{current_id}/checks", json=check_payload()).json()["completed"]
        assert client.get(BASE).json()["completed"] == [FIRST["id"], SECOND["id"]]


@pytest.mark.integration
def test_exact_bank_exposure_and_anchor_gate_guests_and_accounts(course_app, postgres_config):
    app, mail = course_app
    instance_id = "EX-CLOZE-U09"
    item = authored_exercise(instance_id)
    notebook = notebook_at(postgres_config, instance_id, later=12)
    requirements = {word["text"]: word for word in MANIFEST["learner_policy"]["lexical_requirements"][instance_id]}
    with browser(app) as client:
        unseen = {**notebook, "exposures": {}}
        assert instance_id not in guest_call(client, "state", notebook=unseen)["eligible"]
        blocked = client.post(BASE + "/guest", json={"operation": "exercise", "instanceId": instance_id, "notebook": unseen})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "course_vocabulary_not_introduced"
        # Yesterday is a distractor: exposing only the answer's compounds fails.
        missing_distractor = copy.deepcopy(notebook)
        remove_display(missing_distractor, requirements["昨天"]["source_id"])
        assert instance_id not in guest_call(client, "state", notebook=missing_distractor)["eligible"]
        # Later notebook visits cannot retroactively authorize this earlier task.
        too_late = copy.deepcopy(notebook)
        remove_display(too_late, requirements["学校"]["source_id"])
        too_late["exposures"][notebook["completed"][-1]] = [requirements["学校"]["source_id"]]
        assert instance_id not in guest_call(client, "state", notebook=too_late)["eligible"]
        ready = guest_call(client, "state", notebook=notebook)
        assert instance_id in ready["eligible"] and ready["completed"] == []
        run_id = str(uuid4())
        saved = guest_call(client, "save", notebook=notebook, runId=run_id, save=save_payload(item))
        passed = guest_call(client, "check", saved["progress"], notebook=notebook, runId=run_id, check=check_payload(item))
        assert passed["run"]["completed"]  # All earlier optional tasks were skipped.
        retained = guest_call(client, "state", passed["progress"], notebook=None)
        assert retained["completed"] == [instance_id] and retained["eligible"] == []
        assert client.post(BASE + "/guest", json={"operation": "check", "runId": run_id,
            "progress": passed["progress"], "check": check_payload(item)}).status_code == 409
        with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM learning_enrollments")
                assert cursor.fetchone() == (0,)  # Guest validation stays read-only.
        verified_signup(client, mail, "course-exposure@example.com")
        account = client.get("/auth/session").json()["id"]
        assert client.get(BASE).json()["eligible"] == []
        assert client.put(f"{BASE}/runs/{uuid4()}", json=save_payload(item)).status_code == 409
        assert client.post("/learning/guest/import", json={**missing_distractor, "accountId": account}).status_code == 200
        assert instance_id not in client.get(BASE).json()["eligible"]
        assert client.post("/learning/guest/import", json={**notebook, "accountId": account}).status_code == 200
        assert instance_id in client.get(BASE).json()["eligible"]
        account_run = str(uuid4())
        assert client.put(f"{BASE}/runs/{account_run}", json=save_payload(item)).status_code == 200
        assert client.post(f"{BASE}/runs/{account_run}/checks", json=check_payload(item)).json()["completed"]
        assert client.get(BASE).json()["completed"] == [instance_id]


@pytest.mark.integration
def test_homographic_vocabulary_readings_do_not_substitute_for_each_other(course_app, postgres_config):
    instance_id = "EX-CLOZE-U3-21"
    notebook = notebook_at(postgres_config, instance_id)
    anchor = MANIFEST["learner_policy"]["trail_placements"][instance_id]["afterBlockId"]
    correct = next(word for word in MANIFEST["learner_policy"]["lexical_requirements"][instance_id] if word["text"] == "地方")
    wrong = copy.deepcopy(notebook)
    remove_display(wrong, correct["source_id"])
    wrong["exposures"].setdefault(anchor, []).append("cmn-39a718243f4545f7e385d6fe")  # dìfāng: regional, not dìfang: place.
    with browser(course_app[0]) as client:
        assert instance_id not in guest_call(client, "state", notebook=wrong)["eligible"]
        assert instance_id in guest_call(client, "state", notebook=notebook)["eligible"]
        # Slash-separated readings explicitly displayed on the primary card are
        # legitimate alternatives; this task uses the shéi reading of 谁.
        question = notebook_at(postgres_config, "EX-CLOZE-U07")
        assert "EX-CLOZE-U07" in guest_call(client, "state", notebook=question)["eligible"]


@pytest.mark.integration
def test_repeated_vocabulary_exposures_keep_earliest_source_across_import_and_accounts(course_app, postgres_config):
    app, mail = course_app
    instance_id = "EX-CLOZE-U09"
    notebook = notebook_at(postgres_config, instance_id, later=12)
    anchor = MANIFEST["learner_policy"]["trail_placements"][instance_id]["afterBlockId"]
    later = notebook["completed"][-1]
    school_requirement = next(word for word in MANIFEST["learner_policy"]["lexical_requirements"][instance_id] if word["text"] == "学校")
    anchor = school_requirement["introduction_block_id"]
    sources = notebook["exposures"][anchor]
    duplicate = {**notebook, "exposures": {**notebook["exposures"], anchor: sources, later: sources}}
    late_only = {**notebook, "exposures": {**notebook["exposures"], anchor: [], later: sources}}
    with browser(app) as client:
        assert instance_id in guest_call(client, "state", notebook=duplicate)["eligible"]
        verified_signup(client, mail, "course-duplicate-exposure@example.com")
        account = client.get("/auth/session").json()["id"]
        assert client.post("/learning/guest/import", json={**duplicate, "accountId": account}).status_code == 200
        assert instance_id in client.get(BASE).json()["eligible"]
        # A subsequent later-only import must not discard the earlier source.
        assert client.post("/learning/guest/import", json={**late_only, "accountId": account}).status_code == 200
        assert instance_id in client.get(BASE).json()["eligible"]
        assert client.post("/auth/logout", json={}).status_code == 200
        verified_signup(client, mail, "course-later-exposure@example.com")
        account = client.get("/auth/session").json()["id"]
        assert client.get(BASE).json()["eligible"] == []
        assert client.post("/learning/guest/import", json={**late_only, "accountId": account}).status_code == 200
        assert instance_id not in client.get(BASE).json()["eligible"]
        # Existing account evidence on a later card must merge with an earlier
        # explicit guest display, rather than winning merely because it exists.
        assert client.post("/learning/guest/import", json={**duplicate, "accountId": account}).status_code == 200
        assert instance_id in client.get(BASE).json()["eligible"]
        assert client.post("/auth/logout", json={}).status_code == 200
        verified_signup(client, mail, "course-reread-exposure@example.com")
        account = client.get("/auth/session").json()["id"]
        school = next(word["source_id"] for word in MANIFEST["learner_policy"]["lexical_requirements"][instance_id]
                      if word["text"] == "学校")
        school_late = {**notebook, "exposures": {**notebook["exposures"], anchor: [word for word in sources if word != school], later: [school]}}
        assert client.post("/learning/guest/import", json={**school_late, "accountId": account}).status_code == 200
        assert instance_id not in client.get(BASE).json()["eligible"]
        # The normal account display acknowledgement uses the same merge rule.
        vocabulary_url = f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{anchor}/vocabulary"
        displayed = client.get(vocabulary_url)
        assert displayed.status_code == 200, displayed.text
        assert school in {word["id"] for word in displayed.json()["items"]}
        lexical = [word["lexicalUseReference"] for word in displayed.json()["items"] if word["id"] == school and word.get("lexicalUseReference")]
        acknowledged = client.post(vocabulary_url + "/seen", json={"vocabularyIds": [school], "lexicalUses": lexical})
        assert acknowledged.status_code == 200, acknowledged.text
        # Missing prerequisite displays also withhold dependent grammar notes.
        # Reopen those real teaching cards after repairing the word history.
        for _ in range(100):
            state = client.get(BASE).json()
            if instance_id in state['eligible']:
                break
            review = state['grammarReviews'][instance_id]
            url = f"/learning/tracks/{NOTEBOOK['trackId']}/blocks/{review['blockId']}/vocabulary"
            page = client.get(url, params={'limit': 40}).json()
            reply = client.post(url + '/seen', json={
                'vocabularyIds': [word['id'] for word in page['items']],
                'lexicalUses': [word['lexicalUseReference'] for word in page['items'] if word.get('lexicalUseReference')],
                'grammar': [{key: note[key] for key in ['id', 'fingerprint']} for note in page['grammarNotes']]})
            assert reply.status_code == 200, reply.text
        assert instance_id in state['eligible']


@pytest.mark.integration
def test_next_notebook_card_exposures_remain_pending_without_breaking_guest_course_state(course_app):
    completed = NOTEBOOK["completed"][:10]
    notebook = {**NOTEBOOK, "completed": completed, **{key: {block: list(words) for block, words in NOTEBOOK.get(key, {}).items() if block in completed} for key in ["exposures", "grammarExposures", "lexicalUseExposures"]}}
    next_id = NOTEBOOK["completed"][10]  # 的 can be previewed before recording its visit.
    with browser(course_app[0]) as client:
        preview = client.post("/learning/guest/lesson", json={**notebook, "operation": "vocabulary", "blockId": next_id})
        assert preview.status_code == 200, preview.text
        displayed = preview.json()["items"]
        assert any(word["text"] == "是的" for word in displayed)
        notebook["exposures"][next_id] = [word["id"] for word in displayed]
        state = guest_call(client, "state", notebook=notebook)
        assert FIRST["id"] in state["eligible"] and SECOND["id"] not in state["eligible"]
        assert guest_call(client, "exercise", notebook=notebook, instanceId=FIRST["id"])["exercise"]["id"] == FIRST["id"]
        blocked = client.post(BASE + "/guest", json={"operation": "exercise", "notebook": notebook, "instanceId": SECOND["id"]})
        assert blocked.status_code == 409
        # Pending history does not make an unrelated, still-locked source valid.
        invalid = {**notebook, "exposures": {NOTEBOOK["completed"][11]: notebook["exposures"][next_id]}}
        rejected = client.post(BASE + "/guest", json={"operation": "state", "notebook": invalid})
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "learning_invalid_vocabulary"


@pytest.mark.integration
def test_late_scope_current_pass_and_stale_history_remain_independent(course_app, postgres_config):
    """An enriched HSK6 task retains history without accepting its old definition."""
    app, mail = course_app
    item = next(row for row in MANIFEST['exercises'] if row['id'] == 'EX-CLOZE-U6-25')
    notebook = notebook_at(postgres_config, item['id'])
    unseen = copy.deepcopy(notebook)
    unseen['exposures'] = {}
    requirements = {}
    for word in MANIFEST['learner_policy']['lexical_requirements'][item['id']]:
        if not word['primary_block_ids'] and word.get('source_id'):
            requirements.setdefault(word['introduction_block_id'], set()).add(word['source_id'])
    with browser(app) as client:
        # Collect actual public Common words pages at their historical prefixes;
        # knowing the compound's characters alone never prepares the exercise.
        assert item['id'] not in guest_call(client, 'state', notebook=unseen)['eligible']
        for block_id in sorted(requirements, key=notebook['completed'].index):
            remaining = set(requirements[block_id])
            prefix = notebook['completed'][:notebook['completed'].index(block_id) + 1]
            cursor = None
            while remaining:
                response = client.post('/learning/guest/lesson', json={
                    **notebook, **{key: {block: refs for block, refs in notebook.get(key, {}).items() if block in prefix} for key in ['exposures', 'grammarExposures', 'lexicalUseExposures']}, 'completed': prefix, 'operation': 'vocabulary',
                    'blockId': block_id, 'vocabularyAfter': cursor})
                assert response.status_code == 200, response.text
                page = response.json()
                displayed = remaining & {word['id'] for word in page['items']}
                notebook['exposures'].setdefault(block_id, []).extend(sorted(displayed))
                remaining -= displayed
                cursor = page['nextCursor']
                assert not remaining or cursor, f'Missing exact source display at {block_id}'
        assert item['id'] in guest_call(client, 'state', notebook=notebook)['eligible']
        guest_id = str(uuid4())
        saved = guest_call(client, 'save', notebook=notebook, runId=guest_id, save=save_payload(item))
        passed = guest_call(client, 'check', saved['progress'], notebook=notebook,
                            runId=guest_id, check=check_payload(item))
        assert guest_call(client, 'state', passed['progress'], notebook=notebook)['completed'] == [item['id']]
        stale = {'runs': copy.deepcopy(passed['progress']['runs'])}
        stale['runs'][0]['fingerprint'] = '0' * 64
        restored = guest_call(client, 'state', stale, notebook=notebook)
        assert restored['completed'] == []
        archive = restored['progress']['runs'][0]
        assert archive['archived'] and not archive['completed']
        assert archive['attempts'][0]['correct'] is None
        rejected = client.post(BASE + '/guest', json={'operation': 'check', 'notebook': notebook,
            'progress': restored['progress'], 'runId': guest_id, 'check': check_payload(item, fingerprint='0' * 64)})
        assert rejected.status_code == 409

        verified_signup(client, mail, 'late-course-owner@example.com')
        account = client.get('/auth/session').json()['id']
        imported = client.post('/learning/guest/import', json={**notebook, 'accountId': account})
        assert imported.status_code == 200, imported.text
        imported = client.post(BASE + '/import', json={'accountId': account, 'progress': restored['progress']})
        assert imported.status_code == 200 and imported.json()['completed'] == []
        current_id = str(uuid4())
        assert client.put(f'{BASE}/runs/{current_id}', json=save_payload(item)).status_code == 200
        assert client.post(f'{BASE}/runs/{current_id}/checks', json=check_payload(item)).json()['completed']
        assert client.get(BASE).json()['completed'] == [item['id']]
        assert client.get(f"{BASE}/exercises/{item['id']}").json()['run']['id'] == current_id
        # A persisted historical server pass is external database state, separate
        # from both the imported guest archive and the current authored content.
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE learning_course_exercise_runs SET fingerprint=%s WHERE id=%s',
                               ('0' * 64, current_id))
        assert client.get(BASE).json()['completed'] == []
        assert client.get(f"{BASE}/exercises/{item['id']}").json()['run'] is None
        assert client.post(f'{BASE}/runs/{current_id}/checks', json=check_payload(item)).status_code == 409
        fresh_id = str(uuid4())
        assert client.put(f'{BASE}/runs/{fresh_id}', json=save_payload(item)).status_code == 200
        assert client.post(f'{BASE}/runs/{fresh_id}/checks', json=check_payload(item)).json()['completed']
        with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT passed FROM learning_course_exercise_runs WHERE id=%s', (current_id,))
                assert cursor.fetchone() == (True,)
        assert client.post('/auth/logout', json={}).status_code == 200
        verified_signup(client, mail, 'late-course-other@example.com')
        state = client.get(BASE).json()
        assert state['completed'] == [] and state['runs'] == []
        assert client.post(f'{BASE}/runs/{fresh_id}/checks', json=check_payload(item)).status_code == 404
