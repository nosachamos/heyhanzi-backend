import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest
from cryptography.fernet import Fernet
from psycopg2 import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def _isolated_app_config():
    return {
        "GENERAL": {"Environment": "TEST", "DeploymentTimestamp": "test", "ReleaseSha": "abc123"},
        "URLS": {"FrontendUrl": "https://local.heyhanzi.com:3002", "BackendUrl": "https://local-api.heyhanzi.com:8002"},
        "AUTH": {"SecretKey": "test-session-secret-" * 4, "AuditHashSecret": "test-audit-secret-" * 4,
                 "TokenEncryptionKey": Fernet.generate_key().decode()},
        "EMAIL": {"Provider": "fake"},
        "DATABASE": {"Url": "postgresql://test:secret@127.0.0.1:1/unavailable?sslmode=disable"},
    }


# Collection imports the ASGI module before fixtures run. Give that unused
# default app an isolated TEST configuration instead of requiring local secrets.
os.environ.setdefault('APP_CONFIGS', json.dumps(_isolated_app_config()))
os.environ.setdefault('APP_ENV', 'TEST')
os.environ.setdefault('HEYHANZI_DISABLE_DOTENV', 'true')


@pytest.fixture
def app_config():
    return _isolated_app_config()


@pytest.fixture
def postgres_config(app_config):
    # Only explicitly local databases are admissible as integration-test admins.
    admin = make_url(os.getenv(
        "TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/postgres?sslmode=disable"
    ))
    if admin.host not in {"127.0.0.1", "localhost", "::1"} or admin.database != "postgres":
        pytest.fail("TEST_DATABASE_URL must address local disposable PostgreSQL's postgres database")
    name = "heyhanzi_test_" + uuid.uuid4().hex
    connection = psycopg2.connect(admin.render_as_string(hide_password=False), connect_timeout=3)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        app_config["DATABASE"] = {"Url": admin.set(database=name).render_as_string(hide_password=False)}
        yield app_config
    finally:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        connection.close()


@pytest.fixture
def run_cli():
    def invoke(config, *args):
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("STRIPE_", "TURNSTILE_")) and key not in {"DATABASE_URL", "DATABASE_NAME", "MIGRATION_DATABASE_URL", "APP_BASE_URL", "CORS_ORIGINS",
                           "AUTH_SECRET_KEY", "AUTH_AUDIT_HASH_SECRET", "AUTH_TOKEN_ENCRYPTION_KEY"}
        }
        environment.update(APP_CONFIGS=json.dumps(config), APP_ENV=config["GENERAL"]["Environment"],
                           HEYHANZI_DISABLE_DOTENV="true")
        return subprocess.run(
            [sys.executable, *args], cwd=ROOT, env=environment,
            capture_output=True, text=True, timeout=120,
        )
    return invoke


@pytest.fixture
def billing_app(postgres_config, run_cli):
    import httpx

    from app.email.providers import FakeEmailProvider
    from app.main import create_app
    from tests.billing_provider import StripeProvider

    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    postgres_config["STRIPE"] = {"SecretKey": "sk_test_fake", "AnnualPriceId": "price_annual",
                               "WebhookSecret": "whsec_test", "PortalConfigurationId": "bpc_test"}
    provider = StripeProvider()
    mail = FakeEmailProvider()
    return create_app(postgres_config, email_provider=mail, stripe_transport=httpx.MockTransport(provider.handle)), mail, provider
