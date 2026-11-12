import re
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.email.providers import FakeEmailProvider
from app.main import create_app

pytestmark = pytest.mark.integration
ORIGIN = "https://local.heyhanzi.com:3002"
PASSWORD = "A long river sparkles! 974"
NEW_PASSWORD = "Mountain lantern breezes! 682"


def test_sentence_audio_metadata_is_private_and_downloads_need_no_resolution_request(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        verified_signup(client, mail)
        path = client.post("/learning/enrollments", json={"trackId": "mandarin-simplified-v1"}).json()
        block_id = path["stages"][0]["blocks"][0]["id"]
        url = f"/learning/tracks/mandarin-simplified-v1/blocks/{block_id}/vocabulary"
        response = client.get(url)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        examples = response.json()["examples"]
        assert len(examples) == 3
        for example in examples:
            audio = example["audio"]
            assert audio["format"] == "audio-reference-v1"
            assert audio["id"].startswith("sentence-")
            assert re.fullmatch(r"[0-9a-f]{64}", audio["version"])
            assert audio["url"].startswith("https://") and ".cloudfront.net/sentences/" in audio["url"]
            assert 0 < audio["byteLength"] <= 2_000_000
            assert "?" not in audio["url"]
        assert len({e["audio"]["id"] for e in examples}) == 3
        assert client.post("/auth/logout", json={}).status_code == 200
        assert client.get(url).status_code == 401


@pytest.fixture
def auth_app(postgres_config, run_cli):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    return create_app(postgres_config, email_provider=mail), mail


def browser(app):
    return TestClient(app, base_url="https://local-api.heyhanzi.com:8002",
                      headers={"Origin": ORIGIN, "Content-Type": "application/json"})


def signup(client, email="learner@example.com"):
    response = client.post("/auth/email/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.headers["X-CSRF-Token"]
    return response


def wait_email(mail, template, *, after=0):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        messages = [m for m in mail.messages[after:] if m.tags["template"] == template]
        if messages:
            return messages[-1]
        time.sleep(.02)
    raise AssertionError(f"No {template} email delivered")


def email_token(message):
    return re.search(r"#token=([A-Za-z0-9_-]+)", message.text_body).group(1)


def verified_signup(client, mail, email="learner@example.com"):
    after = len(mail.messages)
    signup(client, email)
    token = email_token(wait_email(mail, "welcome_verify", after=after))
    result = client.post("/auth/email/verify", json={"token": token})
    assert result.status_code == 200, result.text
    return token


def test_signup_verify_session_profile_logout(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        response = signup(client, "Learner@EXAMPLE.com")
        assert response.json()["user"]["email"] == "learner@example.com"
        cookies = response.headers.get_list("set-cookie")
        assert any("__Host-heyhanzi_session=" in value and "HttpOnly" in value and "Secure" in value
                   and "SameSite=lax" in value and "Domain=" not in value for value in cookies)
        message = wait_email(mail, "welcome_verify")
        assert "HeyHanzi" in message.subject
        token = email_token(message)
        assert client.get("/auth/email/verify", params={"token": token}).status_code == 200
        # Email scanners and GET requests do not consume tokens or verify users.
        assert client.get("/auth/session").json()["emailVerifiedAt"] is None
        assert client.post("/auth/email/verify", json={"token": token}).json()["user"]["emailVerifiedAt"]
        assert client.post("/auth/email/verify", json={"token": token}).status_code == 400
        assert client.put("/account/profile", json={"name": "小明"}).json()["name"] == "小明"
        assert client.get("/account/security").json()["authMethods"] == ["password"]
        assert client.post("/auth/logout", json={}).status_code == 200
        assert client.get("/auth/session").status_code == 401


def test_csrf_origin_and_input_bounds(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        signup(client)
        missing = client.post("/auth/logout", json={}, headers={"X-CSRF-Token": ""})
        assert missing.status_code == 403
        assert client.post("/auth/logout", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/auth/email/login", content="x" * 17000).status_code == 413
        assert client.post("/auth/email/login", content="email=x", headers={"Content-Type": "text/plain"}).status_code == 415
        secret = "SENSITIVE" * 100
        response = client.post("/auth/email/login", json={"email": "learner@example.com", "password": secret})
        assert response.status_code == 422 and secret not in response.text
        assert client.get("/auth/session").status_code == 200


def test_session_csrf_cannot_be_transplanted(auth_app):
    app, mail = auth_app
    with browser(app) as first:
        signup(first)
        original_csrf = first.headers["X-CSRF-Token"]
        other = first
        result = other.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD})
        assert result.status_code == 200
        other.headers["X-CSRF-Token"] = original_csrf
        other.cookies.set("heyhanzi_csrf", original_csrf)
        assert other.post("/auth/logout", json={}).status_code == 403
        other.headers["X-CSRF-Token"] = other.get("/auth/session").headers["X-CSRF-Token"]
        assert other.post("/auth/logout", json={}).status_code == 200


def test_verification_requires_the_correct_signed_in_account(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        signup(client)
        token = email_token(wait_email(mail, "welcome_verify"))
        original_cookies, original_csrf = dict(client.cookies), client.headers["X-CSRF-Token"]
        client.cookies.clear()
        stranger = client
        assert stranger.post("/auth/email/verify", json={"token": token}).status_code == 401
        signup(stranger, "another@example.com")
        assert stranger.post("/auth/email/verify", json={"token": token}).status_code == 400
        client.cookies.clear()
        client.cookies.update(original_cookies)
        client.headers["X-CSRF-Token"] = original_csrf
        assert client.post("/auth/email/verify", json={"token": token}).status_code == 200


def test_duplicate_signup_does_not_replace_password(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        signup(client)
        duplicate = client.post("/auth/email/signup", json={"email": "learner@example.com", "password": NEW_PASSWORD})
        assert duplicate.status_code == 200 and "user" not in duplicate.json()
        assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": NEW_PASSWORD}).status_code == 401
        assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD}).status_code == 200


def test_reset_is_generic_single_use_and_revokes_all_sessions(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        verified_signup(client, mail)
        original_cookies = dict(client.cookies)
        client.cookies.clear()
        second = client
        assert second.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD}).status_code == 200
        known = client.post("/auth/password/forgot", json={"email": "learner@example.com"})
        absent = client.post("/auth/password/forgot", json={"email": "absent@example.com"})
        assert known.json() == absent.json()
        token = email_token(wait_email(mail, "password_reset"))
        assert client.get("/auth/password/reset/validate", params={"token": token}).status_code == 200
        response = client.post("/auth/password/reset", json={"token": token, "password": NEW_PASSWORD})
        assert response.status_code == 200, response.text
        assert client.get("/auth/session").status_code == 401
        client.cookies.update(original_cookies)
        assert client.get("/auth/session").status_code == 401
        assert client.post("/auth/password/reset", json={"token": token, "password": PASSWORD}).status_code == 400
        assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD}).status_code == 401
        assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": NEW_PASSWORD}).status_code == 200
        assert "changed" in wait_email(mail, "password_changed").subject


def test_password_change_rotates_session_invalidates_old_recovery(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        verified_signup(client, mail)
        old_session = dict(client.cookies)
        client.post("/auth/password/forgot", json={"email": "learner@example.com"})
        token = email_token(wait_email(mail, "password_reset"))
        response = client.post("/account/password/change", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
        assert response.status_code == 200, response.text
        assert response.headers["X-CSRF-Token"] != client.headers["X-CSRF-Token"]
        assert client.get("/auth/session").status_code == 200
        client.cookies.clear()
        client.cookies.update(old_session)
        assert client.get("/auth/session").status_code == 401
        assert client.get("/auth/password/reset/validate", params={"token": token}).status_code == 400


def test_reset_race_only_one_request_can_consume_token(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        verified_signup(client, mail)
        client.post("/auth/password/forgot", json={"email": "learner@example.com"})
        token = email_token(wait_email(mail, "password_reset"))
        def reset(_):
            caller = client
            return caller.post("/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reset, range(2))) == [200, 400]


def test_failed_login_alert_and_persistent_rate_limit(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        verified_signup(client, mail)
        for _ in range(5):
            assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": NEW_PASSWORD}).status_code == 401
        assert "Unsuccessful" in wait_email(mail, "failed_login_alert").subject
        for _ in range(5):
            assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": NEW_PASSWORD}).status_code == 401
        response = client.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD})
        assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0


def test_new_device_alert_and_recognized_device_suppression(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        verified_signup(client, mail)
        result = client.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD})
        assert result.status_code == 200
        wait_email(mail, "new_device_login")
        count = sum(m.tags["template"] == "new_device_login" for m in mail.messages)
        for _ in range(2):
            assert client.post("/auth/email/login", json={"email": "learner@example.com", "password": PASSWORD}).status_code == 200
        time.sleep(2.2)
        assert sum(m.tags["template"] == "new_device_login" for m in mail.messages) == count


def test_learning_warmup_enrollment_and_ordered_persistent_progress(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        tracks = client.get("/learning/tracks").json()["tracks"]
        track = tracks[0]["id"]
        path_url = f"/learning/tracks/{track}/path"
        assert client.get(path_url).status_code == 401
        response = client.post("/auth/email/signup", json={
            "email": "warmup@example.com", "password": PASSWORD, "learningTrackId": track})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.headers["X-CSRF-Token"]
        assert client.get(path_url).status_code == 403
        token = email_token(wait_email(mail, "welcome_verify"))
        assert client.post("/auth/email/verify", json={"token": token}).status_code == 200
        path = client.get(path_url).json()
        words = path["stages"][0]["blocks"]
        assert len(words) == 24 and path["completedCount"] == 0
        assert path["stages"][0]["kind"] == "warmup" and path["stages"][0]["level"] is None
        assert any(len(word["text"]) > 1 for word in words)
        reference = words[0]["languageData"]["strokeOrder"][0]
        assert reference["format"] == "stroke-reference-v1"
        assert reference["languageTag"] == "cmn-Hans-CN"
        for word in words:
            for ref in word["languageData"].get("strokeOrder", []):
                assert set(ref) == {"format", "languageTag", "character", "version"}
        stroke_url = f"/learning/strokes/{reference['languageTag']}/{reference['character']}/{reference['version']}?blockId={words[0]['id']}"
        stroke_response = client.get(stroke_url)
        assert stroke_response.status_code == 200, stroke_response.text
        assert stroke_response.headers["cache-control"] == "no-store"
        assert stroke_response.json()["reference"] == reference
        wo = stroke_response.json()["data"]
        assert client.get(stroke_url.replace("cmn-Hans-CN", "ja-Jpan-JP")).status_code == 404
        assert client.get(stroke_url.replace(reference["version"], "0" * 64)).status_code == 404
        assert words[0]["text"] == wo["character"] == "我"
        assert wo["format"] == "hanzi-writer-v1"
        assert len(wo["strokes"]) == len(wo["medians"]) == 7
        assert all(len(median) >= 2 for median in wo["medians"])
        assert wo["source"]["license"] == "Arphic Public License"
        assert wo["standard"]["sequence"] == "3121534"
        audio = words[0]["pronunciation"]["audio"]

        def assert_audio_reference(ref):
            assert ref["format"] == "audio-reference-v1"
            assert 0 < ref["byteLength"] <= 2_000_000
            if ref.get("url"):
                assert ref["voiceId"] == "mandarin-female"
                assert set(ref["voices"]) == {"mandarin-female", "mandarin-male"}
                for voice, variant in ref["voices"].items():
                    assert variant["voiceId"] == voice
                    assert variant["id"] == ref["id"]
                    assert variant["url"].startswith("https://") and ".cloudfront.net/sentences/" in variant["url"]
                    assert "?" not in variant["url"]
                    assert re.fullmatch(r"[0-9a-f]{64}", variant["version"])
                assert ref["url"] == ref["voices"]["mandarin-female"]["url"]
            else:
                sound = client.get(f"/learning/audio/{ref['languageTag']}/{ref['id']}/{ref['version']}")
                assert sound.status_code == 200
                assert sound.headers["content-type"] == "audio/mpeg"
                assert sound.headers["cache-control"] == "no-store"
                assert len(sound.content) == ref["byteLength"]
                assert hashlib.sha256(sound.content).hexdigest() == ref["version"]

        import hashlib
        assert_audio_reference(audio)
        # Previously issued local references remain playable after CDN activation.
        previous_versions = (
            "0237cbe1af2a75bf831d3625db5532059a71c288bdc5dd26957e4f9f996d2aff",
            "cf3a36ecb102341c0f62fa4de6e7109aee513c203d19a4186b308db5b49b42d8",
            "3d5f00e4f68824a46d367139387e11d6cd085fa59cad1ec40de036b811698d35",
        )
        audio_url = f"/learning/audio/cmn-Hans-CN/wo-wo3/{previous_versions[0]}"
        for previous_version in previous_versions:
            previous_sound = client.get(f"/learning/audio/cmn-Hans-CN/wo-wo3/{previous_version}")
            assert previous_sound.status_code == 200
            assert hashlib.sha256(previous_sound.content).hexdigest() == previous_version
        assert client.get(audio_url.replace("cmn-Hans-CN", "ja-Jpan-JP")).status_code == 404
        assert client.get(audio_url.replace(previous_versions[0], "0" * 64)).status_code == 404
        def assert_character_resources(word, character, count, sequence):
            stroke_ref = word["languageData"]["strokeOrder"][0]
            resource = client.get(f"/learning/strokes/{stroke_ref['languageTag']}/{character}/{stroke_ref['version']}?blockId={word['id']}")
            assert resource.status_code == 200
            geometry = resource.json()["data"]
            assert resource.json()["reference"] == stroke_ref
            assert geometry["character"] == word["text"] == character
            assert len(geometry["strokes"]) == len(geometry["medians"]) == count
            assert geometry["standard"]["sequence"] == sequence
            sound_ref = word["pronunciation"]["audio"]
            assert sound_ref["id"] != audio["id"]
            assert_audio_reference(sound_ref)
        def complete_url(word):
            return f"/learning/tracks/{track}/blocks/{word['id']}/complete"
        assert client.post(complete_url(words[1]), json={}).status_code == 409
        assert client.post(complete_url(words[0]), json={}, headers={"X-CSRF-Token": ""}).status_code == 403
        from tests.learner_journey import complete_required_before
        for index, word in enumerate(words, 1):
            complete_required_before(client, word['id'])
            if word["text"] in ("你", "好"):
                character, count, sequence = {"你": ("你", 7, "3235234"), "好": ("好", 6, "531521")}[word["text"]]
                assert_character_resources(word, character, count, sequence)
            response = client.post(complete_url(word), json={})
            assert response.status_code == 200, response.text
            assert response.json()["completedCount"] == index
            assert response.json()["stages"][0]["blocks"][0]["languageData"]["strokeOrder"] == [reference]
            assert response.json()["stages"][0]["blocks"][0]["pronunciation"]["audio"] == audio
        assert client.post(complete_url(words[0]), json={}).json()["completedCount"] == len(words)
        assert client.post("/learning/enrollments", json={"trackId": track}).json()["completedCount"] == len(words)
        assert client.get(path_url).json()["nextBlockId"] is None
        assert client.get(path_url).headers["cache-control"] == "no-store"
        client.post("/auth/logout", json={})
        assert client.get(stroke_url).status_code == 401
        assert client.get(audio_url).status_code == 401
        after = len(mail.messages)
        signup(client, "other-warmup@example.com")
        token = email_token(wait_email(mail, "welcome_verify", after=after))
        client.post("/auth/email/verify", json={"token": token})
        assert client.get(path_url).status_code == 404
        assert client.post("/learning/enrollments", json={"trackId": track}).json()["completedCount"] == 0


def test_learning_levels_page_windows_and_character_combinations(billing_app):
    app, mail, stripe = billing_app
    with browser(app) as client:
        verified_signup(client, mail, 'character-path@example.com')
        assert client.post('/billing/checkout', json={}).status_code == 200
        stripe.pay()
        assert stripe.event(client).status_code == 200
        track = client.get('/learning/tracks').json()['tracks'][0]['id']
        client.post('/learning/enrollments', json={'trackId': track})
        url = f'/learning/tracks/{track}'
        first = client.get(url + '/journey').json()
        assert [level['id'] for level in first['levels']] == ['warmup'] + [f'hsk-{n}' for n in range(1, 7)]
        assert first['selectedLevelId'] == 'warmup'
        jiu_page = client.get(url + f'/journey?level=hsk-2&around={track}-char-{ord("就"):x}').json()
        jiu = next(block for stage in jiu_page['stages'] for block in stage['blocks'] if block['text'] == '就')
        assert jiu['translations']['en'] == 'then; right away; just'
        assert jiu['translations']['zh'] == '就'
        assert jiu['pronunciation']['value'] == 'jiù'
        initial = [block for stage in first['stages'] for block in stage['blocks']]
        assert len(initial) <= 20 and all(block['kind'] == 'word' for block in initial)
        assert client.get(url + '/journey?limit=1000').status_code == 422
        future = client.get(url + '/journey?level=hsk-6').json()
        blocks = [block for stage in future['stages'] for block in stage['blocks']]
        assert len(blocks) <= 20 and all(block['status'] == 'locked' for block in blocks)
        next_page = client.get(url + f"/journey?level=hsk-6&after={future['window']['afterCursor']}").json()
        following = [block for stage in next_page['stages'] for block in stage['blocks']]
        assert following and {block['id'] for block in following}.isdisjoint(block['id'] for block in blocks)
        assert client.post(url + f"/blocks/{blocks[0]['id']}/complete", json={}).status_code == 409
        warmup = client.get(url + '/path').json()['stages'][0]['blocks']
        learned = set()
        displayed_words = set()
        person_card = None
        from tests.learner_journey import complete_required_before
        for block in warmup:
            complete_required_before(client, block['id'])
            learned.update(block['languageData']['characters'])
            displayed_words.add(block['text'])
            examples_response = client.get(url + f"/blocks/{block['id']}/vocabulary")
            assert examples_response.status_code == 200
            examples = examples_response.json()
            if block['text'] == '人':
                good_person = next(word for word in examples['items'] if word['text'] == '好人')
                assert good_person['pronunciation']['value'] == 'hǎo rén'
                assert good_person['translations']['en'] == 'good person'
                assert good_person['newlyAvailable']
                assert examples['example']['text'] == '我是好人。'
                person_card = (block['id'], [word['id'] for word in examples['items']])
            if block['text'] == '他':
                # Expand this card to teach the missing prerequisite instead of
                # restricting the sentence. GET alone still cannot record exposure.
                assert examples['example']['text'] == '他是好人。'
                support = next(word for word in examples['items'] if word['text'] == '好人')
                assert support['supportsExample'] and support['newlyAvailable']
                for _ in range(2):
                    assert client.post(url + f'/blocks/{person_card[0]}/vocabulary/seen',
                        json={'vocabularyIds': person_card[1]}).status_code == 200
                examples = client.get(url + f"/blocks/{block['id']}/vocabulary").json()
                assert examples['example']['text'] == '他是好人。'
                assert not next(word for word in examples['items'] if word['text'] == '好人')['newlyAvailable']
            if block['text'] == '她':
                assert examples['example']['text'] == '她是好人。'
            allowed_words = displayed_words | {word['text'] for word in examples['items']}
            assert 1 <= len(examples['examples']) <= 3
            assert len({sentence['text'] for sentence in examples['examples']}) == len(examples['examples'])
            for sentence in examples['examples'] + [word['example'] for word in examples['items'] if word['example'] is not None]:
                assert sentence and sentence['text'] == ''.join(token['text'] for token in sentence['tokens'])
                assert '我在学“' not in sentence['text']
                assert sentence['english'] and sentence['pinyin']
                for token in sentence['tokens']:
                    assert all(0 <= start < end <= len(sentence['english']) for start, end in token['englishRanges'])
                assert {character for character in sentence['text'] if '\u3400' <= character <= '\u9fff'} <= learned
                assert any(token['highlighted'] for token in sentence['tokens'])
                assert all(token['text'] in allowed_words for token in sentence['tokens']
                    if any('\u3400' <= character <= '\u9fff' for character in token['text']))
            if block['text'] == '好':
                assert examples['example']['text'] == '你好！'
                assert [token['text'] for token in examples['example']['tokens'] if token['highlighted']] == ['你好']
            if examples['items'] and block['text'] != '人':
                assert client.post(url + f"/blocks/{block['id']}/vocabulary/seen",
                    json={'vocabularyIds': [word['id'] for word in examples['items']]}).status_code == 200
            displayed_words.update(word['text'] for word in examples['items'])
            assert client.post(url + f"/blocks/{block['id']}/complete", json={}).status_code == 200
        person = next(block for block in warmup if block['text'] == '人')
        combinations = client.get(url + f"/blocks/{person['id']}/vocabulary?limit=40").json()['items']
        other_person = next(word for word in combinations if word['text'] == '他人')
        assert other_person['translations']['en'] == 'another person; somebody else; other people'
        assert other_person['translations']['zh'] == '他人'
        from tests.learner_journey import complete_required_warmup_stories
        complete_required_warmup_stories(client)
        for character, expected, excluded in [('老', '老人', '老师'), ('师', '老师', '老公'), ('公', '老公', '老婆')]:
            response = client.get(url + '/journey?level=hsk-1')
            assert response.headers['cache-control'] == 'no-store'
            path = response.json()
            current = next(block for stage in path['stages'] for block in stage['blocks'] if block['id'] == path['nextBlockId'])
            assert current['text'] == character
            vocabulary_url = url + f"/blocks/{current['id']}/vocabulary"
            vocabulary_response = client.get(vocabulary_url).json()
            words = vocabulary_response['items']
            sentence = vocabulary_response['example']
            assert [token['text'] for token in sentence['tokens'] if token['highlighted']] == [expected]
            word_example = next(word for word in words if word['text'] == expected)['example']
            if word_example is not None:
                assert expected in {token['text'] for token in word_example['tokens']}
                assert '我在学“' not in word_example['text']
            assert sum(word['text'] == expected for word in words) == 1
            assert excluded not in {word['text'] for word in words}
            assert next(word for word in words if word['text'] == expected)['newlyAvailable']
            assert client.post(url + f"/blocks/{current['id']}/complete", json={}).status_code == 200
        reread = client.get(url + f"/blocks/{track}-char-{ord('老'):x}/vocabulary").json()['items']
        assert not next(word for word in reread if word['text'] == '老公')['newlyAvailable']
        client.post('/auth/logout', json={})
        assert client.get(url + '/journey').status_code == 401
        assert client.get(vocabulary_url).status_code == 401
        assert client.post(vocabulary_url + '/seen', json={'vocabularyIds': person_card[1]}).status_code == 401
        verified_signup(client, mail, 'separate-vocabulary@example.com')
        client.post('/learning/enrollments', json={'trackId': track})
        for block in warmup[:6]:
            complete_required_before(client, block['id'])
            assert client.post(url + f"/blocks/{block['id']}/complete", json={}).status_code == 200
        her = warmup[6]
        isolated = client.get(url + f"/blocks/{her['id']}/vocabulary").json()
        assert isolated['example']['text'] == '她是好人。'
        support = next(word for word in isolated['items'] if word['text'] == '好人')
        assert support['supportsExample'] and support['newlyAvailable']
        # Save only the displayed first page, and keep later pages unread. Saving
        # must not change the order of remaining vocabulary or count it as taught.
        person_id = warmup[4]['id']
        first_page = client.get(url + f'/blocks/{person_id}/vocabulary?limit=1').json()
        assert [word['text'] for word in first_page['items']] == ['好人']
        cursor = first_page['nextCursor']
        second_url = url + f'/blocks/{person_id}/vocabulary?limit=1&after={cursor}'
        second = client.get(second_url).json()
        assert second['items'] and second['items'][0]['id'] != support['id']
        assert client.post(url + f'/blocks/{person_id}/vocabulary/seen',
            json={'vocabularyIds': [support['id']]}).status_code == 200
        assert [word['id'] for word in client.get(second_url).json()['items']] == [word['id'] for word in second['items']]
        assert not next(word for word in client.get(url + f"/blocks/{her['id']}/vocabulary").json()['items']
                        if word['text'] == '好人')['newlyAvailable']
        future_word = next(word['id'] for word in words if word['text'] == '老公')
        assert client.post(url + f"/blocks/{her['id']}/vocabulary/seen",
            json={'vocabularyIds': [future_word]}).status_code == 422


def test_guest_lessons_are_anonymous_and_import_is_additive_and_validated(auth_app):
    app, mail = auth_app
    with browser(app) as client:
        track = client.get('/learning/tracks').json()['tracks'][0]['id']
        progress = {'trackId': track, 'completed': [], 'exposures': {}}
        response = client.post('/learning/guest/lesson', json=progress)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        assert not client.cookies
        path = response.json()
        assert path['completedCount'] == 0 and path['hasUnlimitedAccess'] is False
        words = [b for stage in path['stages'] for b in stage['blocks'] if b['kind'] == 'word']
        first, second = words[:2]
        assert client.post('/learning/guest/lesson', json={**progress, 'completed': [second['id']]}).status_code == 422
        assert client.post('/learning/guest/lesson', json={**progress, 'completed': ['made-up']}).status_code == 422
        assert client.post('/learning/guest/import', json=progress).status_code == 401
        stroke = client.post('/learning/guest/lesson', json={**progress, 'operation': 'strokes',
            'blockId': first['id'], 'reference': first['languageData']['strokeOrder'][0]})
        assert stroke.status_code == 200 and stroke.json()['data']['character'] == first['text']
        vocabulary = client.post('/learning/guest/lesson', json={**progress, 'operation': 'vocabulary', 'blockId': first['id']})
        assert vocabulary.status_code == 200 and vocabulary.json()['examples']
        progress['completed'] = [first['id'], second['id']]
        progress['exposures'] = {first['id']: [w['id'] for w in vocabulary.json()['items']]}
        assert client.post('/learning/guest/lesson', json=progress).json()['completedCount'] == 2
        # A longer saved history is accepted beyond the small auth-body limit.
        large = {**progress, 'completed': [first['id']] * 2000, 'exposures': {}}
        assert client.post('/learning/guest/lesson', json=large).json()['completedCount'] == 1
        all_words = list(words)
        window = path
        while window['window']['afterCursor'] is not None:
            window = client.post('/learning/guest/lesson', json={**progress, 'after': window['window']['afterCursor']}).json()
            all_words.extend(block for stage in window['stages'] for block in stage['blocks'] if block['kind'] == 'word')
        warmup = {**progress, 'completed': [word['id'] for word in all_words], 'exposures': {}}
        hsk = client.post('/learning/guest/lesson', json={**warmup, 'level': 'hsk-1'})
        assert hsk.status_code == 200, hsk.text
        hsk_path = hsk.json()
        assert hsk_path['nextLevelId'] == 'hsk-1' and hsk_path['hasUnlimitedAccess'] is False
        next_word = next(block for stage in hsk_path['stages'] for block in stage['blocks'] if block['id'] == hsk_path['nextBlockId'])
        assert client.post('/learning/guest/lesson', json={**warmup, 'operation': 'vocabulary', 'blockId': next_word['id']}).status_code == 200
        assert client.post('/learning/guest/lesson', json={**warmup, 'operation': 'strokes', 'blockId': next_word['id'],
            'reference': first['languageData']['strokeOrder'][0]}).status_code == 404
        assert client.get('/auth/session').status_code == 401
        verified_signup(client, mail)
        progress['accountId'] = client.get('/auth/session').json()['id']
        imported = client.post('/learning/guest/import', json=progress)
        assert imported.status_code == 200, imported.text
        assert client.get(f'/learning/tracks/{track}/journey').json()['completedCount'] == 2
        # Existing account progress must survive an older guest import.
        client.post('/learning/enrollments', json={'trackId': track})
        from tests.learner_journey import complete_required_before
        for word in words[:3]:
            complete_required_before(client, word['id'])
            assert client.post(f"/learning/tracks/{track}/blocks/{word['id']}/complete", json={}).status_code == 200
        progress['accountId'] = client.get('/auth/session').json()['id']
        assert client.post('/learning/guest/import', json={**progress, 'accountId': 'another-account'}).status_code == 409
        for _ in range(2):
            imported = client.post('/learning/guest/import', json=progress)
            assert imported.status_code == 200, imported.text
        account = client.get(f'/learning/tracks/{track}/journey').json()
        assert account['completedCount'] == 3
        invalid = {**progress, 'exposures': {first['id']: ['invented-vocabulary']}}
        assert client.post('/learning/guest/import', json=invalid).status_code == 422
        assert client.get(f'/learning/tracks/{track}/journey').json()['completedCount'] == 3
        client.headers['X-CSRF-Token'] = 'wrong'
        assert client.post('/learning/guest/import', json=progress).status_code == 403
