import psycopg2
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database.database import get_db
from app.main import create_app

pytestmark = pytest.mark.integration


def test_migrations_are_repeatable_and_preserve_data(postgres_config, run_cli):
    with TestClient(create_app(postgres_config)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
    for _ in range(2):
        result = run_cli(postgres_config, "scripts/bootstrap_database.py")
        assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE rehearsal_sentinel (value text NOT NULL)")
            cursor.execute("INSERT INTO rehearsal_sentinel VALUES ('keep-me')")
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM rehearsal_sentinel")
            assert cursor.fetchall() == [("keep-me",)]
            cursor.execute("SELECT version_num FROM alembic_version")
            current_revisions = cursor.fetchall()
    heads = run_cli(postgres_config, '-m', 'alembic', 'heads')
    assert heads.returncode == 0, heads.stderr
    assert current_revisions == [(heads.stdout.split()[0],)]
    with TestClient(create_app(postgres_config)) as client:
        assert client.get("/ready").json() == {"status": "ready"}


def test_schema_matches_models_and_destructive_downgrade_is_rejected(postgres_config, run_cli):
    for arguments in [('upgrade', 'head'), ('check',)]:
        result = run_cli(postgres_config, '-m', 'alembic', *arguments)
        assert result.returncode == 0, result.stderr
    rejected = run_cli(postgres_config, '-m', 'alembic', 'downgrade', 'base')
    assert rejected.returncode != 0 and 'forward migration' in rejected.stderr
    # Failed destructive rollback leaves the schema current and reusable.
    for arguments in [('upgrade', 'head'), ('check',)]:
        result = run_cli(postgres_config, '-m', 'alembic', *arguments)
        assert result.returncode == 0, result.stderr


def test_readiness_rejects_an_incorrect_schema_revision(postgres_config, run_cli):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE alembic_version SET version_num = 'stale_revision'")
    with TestClient(create_app(postgres_config)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503


def test_request_transaction_rollback_and_pool_recovery(postgres_config, run_cli):
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE transaction_probe (value text NOT NULL)")
    app = create_app(postgres_config)

    @app.post("/transaction-probe/{outcome}")
    async def transact(outcome: str, session=Depends(get_db)):
        await session.execute(text("INSERT INTO transaction_probe VALUES (:value)"), {"value": outcome})
        if outcome == "fail":
            raise RuntimeError("Intentional failure after a write")
        await session.commit()
        return {"saved": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(5):
            assert client.post("/transaction-probe/fail").status_code == 500
        assert client.post("/transaction-probe/success").status_code == 200
        assert client.get("/ready").status_code == 200
    with psycopg2.connect(postgres_config["DATABASE"]["Url"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM transaction_probe")
            assert cursor.fetchall() == [("success",)]
