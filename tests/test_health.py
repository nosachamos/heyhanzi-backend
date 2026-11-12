import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_does_not_depend_on_database(app_config):
    with TestClient(create_app(app_config)) as client:
        response = client.get("/health", headers={"X-Request-ID": "frontend-request-1"})
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok", "service": "heyhanzi-backend",
            "deploymentTimestamp": "test", "releaseSha": "abc123",
        }
        assert response.headers["X-Request-ID"] == "frontend-request-1"
        assert response.headers["Cache-Control"] == "no-store"
        assert client.get("/").json() == response.json()
        assert client.get("/ready").status_code == 503
        assert client.get("/ready").json() == {"detail": "Database unavailable"}
        assert client.get("/health").status_code == 200


def test_frontend_cors_allows_credentials_and_preflight(app_config):
    origin = "https://local.heyhanzi.com:3002"
    with TestClient(create_app(app_config)) as client:
        response = client.options("/health", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type,x-request-id",
        })
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        assert "x-request-id" in response.headers["Access-Control-Allow-Headers"]
        error = client.get("/ready", headers={"Origin": origin})
        assert error.status_code == 503
        assert error.headers["Access-Control-Allow-Origin"] == origin
        exposed = error.headers["Access-Control-Expose-Headers"].lower()
        assert "x-csrf-token" in exposed
        assert "retry-after" in exposed


@pytest.mark.parametrize("origin", ["https://evil.example", "https://local.heyhanzi.com.evil.example:3002"])
def test_unapproved_origins_cannot_read_responses(app_config, origin):
    with TestClient(create_app(app_config)) as client:
        response = client.options("/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert response.status_code == 400
        assert "Access-Control-Allow-Origin" not in response.headers
        assert "Access-Control-Allow-Origin" not in client.get("/health", headers={"Origin": origin}).headers


def test_invalid_request_id_replaced_and_docs_development_only(app_config):
    with TestClient(create_app(app_config)) as client:
        response = client.get("/health", headers={"X-Request-ID": "unsafe " * 20})
        assert len(response.headers["X-Request-ID"]) == 32
        assert client.get("/docs").status_code == 404
    app_config["GENERAL"]["Environment"] = "DEV"
    with TestClient(create_app(app_config)) as client:
        assert client.get("/docs").status_code == 200
        assert "/health" in client.get("/openapi.json").json()["paths"]
