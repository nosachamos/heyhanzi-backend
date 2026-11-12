import asyncio
from datetime import UTC, datetime, timedelta

import boto3
import psycopg2
import pytest
from botocore.stub import Stubber

from app.email.config import email_config_from_source
from app.email.providers import FakeEmailProvider, SesEmailProvider
from app.email.templates import AuthEmailContext, AuthEmailTemplate, build_auth_email_message
from app.main import create_app
from tests.test_auth_flows import NEW_PASSWORD, browser, email_token, signup, wait_email


def test_ses_sends_branded_html_and_text_with_explicit_client(app_config):
    app_config["EMAIL"] = {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com", "FromName": "HeyHanzi"}
    message = build_auth_email_message(AuthEmailTemplate.PASSWORD_RESET,
        AuthEmailContext(to_email="learner@example.com", user_name="<script>alert(1)</script>",
                         action_url="https://heyhanzi.com/reset-password#token=example"), config=app_config)
    assert "<script>" not in message.html_body
    assert "&lt;script&gt;" in message.html_body
    assert "HeyHanzi" in message.subject
    client = boto3.client("sesv2", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    provider = SesEmailProvider(email_config_from_source(app_config), client=client)
    with Stubber(client) as stub:
        stub.add_response("send_email", {"MessageId": "accepted-email"}, {
            "FromEmailAddress": "HeyHanzi <no-reply@heyhanzi.com>",
            "Destination": {"ToAddresses": ["learner@example.com"]},
            "Content": {"Simple": {"Subject": {"Data": message.subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": message.html_body, "Charset": "UTF-8"},
                         "Text": {"Data": message.text_body, "Charset": "UTF-8"}}}},
        })
        assert asyncio.run(provider.send(message)).message_id == "accepted-email"
        stub.assert_no_pending_responses()


class FlakyProvider(FakeEmailProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def send(self, message):
        self.calls += 1
        if self.calls == 1:
            raise OSError("temporary provider outage")
        return await super().send(message)


@pytest.mark.integration
def test_email_outage_retries_from_encrypted_durable_queue(postgres_config, run_cli, monkeypatch):
    import time
    now = [datetime.now(UTC)]
    monkeypatch.setattr("app.utils.time.utc_now", lambda: now[0])
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    mail = FlakyProvider()
    app = create_app(postgres_config, email_provider=mail)
    with browser(app) as client:
        signup(client)
        deadline = time.monotonic() + 8
        while mail.calls == 0 and time.monotonic() < deadline:
            time.sleep(.02)
        assert mail.calls == 1 and not mail.messages
    # Restart the entire application after delivery fails; the queue survives.
    now[0] += timedelta(minutes=2)
    with browser(create_app(postgres_config, email_provider=mail)):
        message = wait_email(mail, "welcome_verify")
        assert mail.calls == 2
        token = email_token(message)
        with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT token_hash FROM auth_tokens")
                assert token not in str(cursor.fetchall())


@pytest.mark.integration
def test_expiry_and_resend_supersession(postgres_config, run_cli, monkeypatch):
    now = [datetime.now(UTC)]
    monkeypatch.setattr("app.utils.time.utc_now", lambda: now[0])
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    with browser(create_app(postgres_config, email_provider=mail)) as client:
        signup(client)
        token = email_token(wait_email(mail, "welcome_verify"))
        count = len(mail.messages)
        assert client.post("/account/email-verification/resend", json={}).status_code == 200
        replacement = email_token(wait_email(mail, "welcome_verify", after=count))
        assert replacement != token
        assert client.get("/auth/email/verify", params={"token": token}).status_code == 400
        now[0] += timedelta(hours=25)
        assert client.post("/auth/email/verify", json={"token": replacement}).status_code == 400
        assert client.get("/auth/session").status_code == 200
        now[0] += timedelta(days=31)
        assert client.get("/auth/session").status_code == 401


@pytest.mark.integration
def test_rate_limits_survive_process_restart(postgres_config, run_cli):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    for iteration in range(2):
        with browser(create_app(postgres_config)) as client:
            if iteration == 0:
                for _ in range(3):
                    assert client.post("/auth/password/forgot", json={"email": "missing@example.com"}).status_code == 200
            else:
                assert client.post("/auth/password/forgot", json={"email": "missing@example.com"}).status_code == 429


@pytest.mark.integration
def test_forged_proxy_headers_do_not_bypass_network_limits(postgres_config, run_cli):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    with browser(create_app(postgres_config)) as client:
        for attempt in range(31):
            response = client.post("/auth/password/forgot", json={"email": f"missing-{attempt}@example.com"},
                headers={"X-Forwarded-For": f"192.0.2.{attempt + 1}", "Fly-Client-IP": f"192.0.2.{attempt + 1}"})
            assert response.status_code == (200 if attempt < 30 else 429)


@pytest.mark.integration
def test_password_reset_expires_without_changing_credentials(postgres_config, run_cli, monkeypatch):
    now = [datetime.now(UTC)]
    monkeypatch.setattr("app.utils.time.utc_now", lambda: now[0])
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    mail = FakeEmailProvider()
    with browser(create_app(postgres_config, email_provider=mail)) as client:
        signup(client)
        assert client.post("/auth/password/forgot", json={"email": "learner@example.com"}).status_code == 200
        token = email_token(wait_email(mail, "password_reset"))
        now[0] += timedelta(minutes=31)
        assert client.get("/auth/password/reset/validate", params={"token": token}).status_code == 400
        assert client.post("/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}).status_code == 400
        assert client.get("/auth/session").status_code == 200
