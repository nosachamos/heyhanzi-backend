import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.email.providers import FakeEmailProvider
from app.main import create_app

ORIGIN = "https://local.heyhanzi.com:3002"
PASSWORD = "A long river sparkles! 974"
SETTINGS = {"Enabled": True, "SiteKey": "production-site-fixture",
            "SecretKey": "production-secret-fixture", "Hostnames": ["heyhanzi.com"]}


def production(config):
    config["GENERAL"]["Environment"] = "PROD"
    config["DATABASE"]["Url"] = "postgresql://user:password@db.example.com/heyhanzi?sslmode=require"
    config["EMAIL"] = {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com", "FromName": "HeyHanzi",
                       "SupportUrl": "https://heyhanzi.com", "SesRegion": "us-east-1"}
    config["AWS"] = {"AccessKeyId": "test-access", "SecretAccessKey": "test-secret"}
    return config


@pytest.mark.parametrize("settings", [None, {}, {**SETTINGS, "SecretKey": ""},
    {**SETTINGS, "Enabled": False, "SecretKey": ""},
    {**SETTINGS, "SiteKey": "1x00000000000000000000AA"},
    {**SETTINGS, "SecretKey": "1x0000000000000000000000000000000AA"},
    {**SETTINGS, "Hostnames": ["localhost"]}, {**SETTINGS, "Hostnames": ["local.heyhanzi.com"]},
    {**SETTINGS, "Hostnames": ["*.heyhanzi.com"]}, {**SETTINGS, "Hostnames": ["https://heyhanzi.com"]},
    {**SETTINGS, "Hostnames": []},
])
def test_production_refuses_missing_or_unsafe_turnstile(app_config, settings):
    production(app_config)
    if settings is not None:
        app_config["TURNSTILE"] = settings
    with pytest.raises(ValueError, match="TURNSTILE"):
        create_app(app_config)


def test_production_cannot_disable_verification_and_policy_never_exposes_secret(app_config):
    production(app_config)
    app_config["TURNSTILE"] = {**SETTINGS, "Enabled": False}
    # This public policy route requires no database or provider connection.
    client = TestClient(create_app(app_config))
    response = client.get("/auth/security")
    assert response.json() == {"required": True, "siteKey": SETTINGS["SiteKey"]}
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in response.text


@pytest.mark.parametrize("environment", ["DEV", "TEST"])
def test_only_explicit_local_environments_allow_disabled_policy(app_config, environment):
    app_config["GENERAL"]["Environment"] = environment
    response = TestClient(create_app(app_config)).get("/auth/security")
    assert response.json() == {"required": False, "siteKey": None}


class Cloudflare:
    def __init__(self):
        self.calls = []
        self.used = set()
        self.result = None

    def handle(self, request):
        assert str(request.url) == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        body = json.loads(request.content)
        self.calls.append(body)
        assert body["secret"] == SETTINGS["SecretKey"]
        if isinstance(self.result, Exception):
            raise self.result
        if isinstance(self.result, bytes):
            return httpx.Response(200, content=self.result)
        if self.result is not None:
            return httpx.Response(200, json=self.result)
        token = body["response"]
        if token in self.used:
            return httpx.Response(200, json={"success": False, "error-codes": ["timeout-or-duplicate"]})
        self.used.add(token)
        return httpx.Response(200, json={"success": True, "hostname": "heyhanzi.com",
            "action": token.split(":")[0], "challenge_ts": datetime.now(timezone.utc).isoformat()})


@pytest.fixture
def secured_app(postgres_config, run_cli):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    postgres_config["TURNSTILE"] = copy.deepcopy(SETTINGS)
    provider = Cloudflare()
    mail = FakeEmailProvider()
    return create_app(postgres_config, email_provider=mail,
                      turnstile_transport=httpx.MockTransport(provider.handle)), provider


def browser(app):
    return TestClient(app, base_url="https://local-api.heyhanzi.com:8002",
                      headers={"Origin": ORIGIN, "Content-Type": "application/json"})


@pytest.mark.integration
def test_all_protected_actions_require_fresh_action_bound_tokens(secured_app):
    app, provider = secured_app
    credentials = {"email": "learner@example.com", "password": PASSWORD}
    with browser(app) as client:
        denied = client.post("/auth/email/signup", json=credentials)
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "turnstile_failed"
        assert client.get("/auth/session").status_code == 401
        assert provider.calls == []
        signed_up = client.post("/auth/email/signup", json={**credentials, "turnstileToken": "signup:1"})
        assert signed_up.status_code == 200, signed_up.text
        client.headers["X-CSRF-Token"] = signed_up.headers["X-CSRF-Token"]
        assert client.post("/account/email-verification/resend", json={}).status_code == 403
        assert client.post("/account/email-verification/resend", json={"turnstileToken": "resend_verification:1"}).status_code == 200
        assert client.post("/auth/logout", json={}).status_code == 200
        assert client.post("/auth/email/login", json=credentials).status_code == 403
        assert client.post("/auth/email/login", json={**credentials, "turnstileToken": "signup:2"}).status_code == 403
        logged_in = client.post("/auth/email/login", json={**credentials, "turnstileToken": "login:1"})
        assert logged_in.status_code == 200
        client.headers["X-CSRF-Token"] = logged_in.headers["X-CSRF-Token"]
        assert client.post("/auth/password/forgot", json={"email": credentials["email"]}).status_code == 403
        payload = {"email": credentials["email"], "turnstileToken": "forgot_password:1"}
        assert client.post("/auth/password/forgot", json=payload).status_code == 200
        assert client.post("/auth/password/forgot", json=payload).status_code == 403
        # An invalid challenge does not grant a fresh login session.
        assert client.post("/auth/logout", json={}).status_code == 200
        assert client.post("/auth/email/login", json={**credentials, "turnstileToken": "login:1"}).status_code == 403
        assert client.get("/auth/session").status_code == 401


@pytest.mark.integration
def test_untrusted_or_unavailable_provider_never_allows_operation(secured_app, capsys):
    app, provider = secured_app
    now = datetime.now(timezone.utc)
    valid = {"success": True, "hostname": "heyhanzi.com", "action": "forgot_password", "challenge_ts": now.isoformat()}
    cases = [
        ({**valid, "hostname": "attacker.example"}, 403), ({**valid, "action": "signup"}, 403),
        ({**valid, "challenge_ts": (now - timedelta(seconds=301)).isoformat()}, 403),
        ({**valid, "challenge_ts": (now + timedelta(minutes=5)).isoformat()}, 403),
        ({**valid, "challenge_ts": "invalid"}, 403), ({**valid, "challenge_ts": None}, 403),
        ({"success": False, "error-codes": ["invalid-input-response"]}, 403),
        ({"success": False, "error-codes": ["timeout-or-duplicate"]}, 403),
        ({"success": False, "error-codes": ["invalid-input-secret"]}, 503),
        ({"success": "true"}, 503), ([], 503), (b"not-json", 503), (b"x" * 16385, 503),
        (httpx.ReadTimeout("provider unavailable"), 503),
    ]
    with browser(app) as client:
        for index, (result, status) in enumerate(cases):
            provider.result = result
            before = len(provider.calls)
            response = client.post("/auth/password/forgot", json={"email": f"test{index}@example.com", "turnstileToken": "synthetic-private-token"})
            assert response.status_code == status, response.text
            calls = provider.calls[before:]
            assert len(calls) == (2 if status == 503 else 1)
            assert len({call["idempotency_key"] for call in calls}) == 1
        assert client.get("/auth/session").status_code == 401
    output = capsys.readouterr()
    assert "synthetic-private-token" not in output.out + output.err
    assert SETTINGS["SecretKey"] not in output.out + output.err


@pytest.mark.integration
def test_limits_and_request_boundaries_precede_provider(secured_app):
    app, provider = secured_app
    with browser(app) as client:
        path = "/auth/password/forgot"
        payload = {"email": "limit@example.com", "turnstileToken": "forgot_password:1"}
        assert client.post(path, json=payload, headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.post(path, json={**payload, "turnstileToken": "x" * 2049}).status_code == 422
        assert provider.calls == []
        for _ in range(3):
            assert client.post(path, json={"email": payload["email"]}).status_code == 403
        blocked = client.post(path, json=payload)
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
        assert provider.calls == []
