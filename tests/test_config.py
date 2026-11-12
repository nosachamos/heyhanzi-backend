import os
import subprocess
import sys

import pytest

from app.main import create_app


@pytest.mark.parametrize("origin", ["*", "http://heyhanzi.com", "https://heyhanzi.com/path"])
def test_production_rejects_invalid_frontend_origins(app_config, origin):
    app_config["GENERAL"]["Environment"] = "PROD"
    app_config["URLS"]["FrontendUrl"] = origin
    with pytest.raises(ValueError, match="origin|HTTPS"):
        create_app(app_config)


def test_production_requires_encrypted_database(app_config):
    app_config["GENERAL"]["Environment"] = "PROD"
    app_config["URLS"]["FrontendUrl"] = "https://heyhanzi.com"
    app_config["DATABASE"]["Url"] = "postgresql://user:private-secret@db.example.com/heyhanzi?sslmode=disable"
    with pytest.raises(ValueError, match="TLS"):
        create_app(app_config)


def test_database_name_cannot_redirect_connection(app_config):
    app_config["DATABASE"]["DatabaseName"] = "another-project"
    with pytest.raises(ValueError, match="must match"):
        create_app(app_config)


def test_production_does_not_fall_back_to_development_config():
    environment = {**os.environ, "APP_ENV": "PROD", "APP_CONFIGS": "", "HEYHANZI_DISABLE_DOTENV": "true"}
    result = subprocess.run([sys.executable, "scripts/validate_runtime_config.py"], env=environment,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "APP_CONFIGS is required" in result.stderr


def test_malformed_secret_config_is_not_echoed():
    environment = {**os.environ, "APP_CONFIGS": '{"secret":"dont-print-this"', "APP_ENV": "PROD"}
    result = subprocess.run([sys.executable, "scripts/validate_runtime_config.py"], env=environment,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "dont-print-this" not in result.stdout + result.stderr


def test_production_config_validation_accepts_render_and_percent_password(app_config, run_cli):
    app_config["GENERAL"]["Environment"] = "PROD"
    app_config["URLS"]["FrontendUrl"] = "https://heyhanzi.com"
    app_config["EMAIL"] = {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com", "FromName": "HeyHanzi",
                           "SupportUrl": "https://heyhanzi.com", "SesRegion": "us-east-1"}
    app_config["TURNSTILE"] = {"SiteKey": "production-site-fixture", "SecretKey": "production-secret-fixture", "Hostnames": ["heyhanzi.com"]}
    app_config["AWS"] = {"AccessKeyId": "test-access", "SecretAccessKey": "test-secret"}
    app_config["DATABASE"]["Url"] = "postgres://user:percent%25encoded@db.ohio-postgres.render.com/heyhanzi?sslmode=verify-full"
    result = run_cli(app_config, "scripts/validate_runtime_config.py")
    assert result.returncode == 0, result.stderr
    assert "percent" not in result.stdout


@pytest.mark.parametrize("environment,key_mode,accepted", [
    ("DEV", "test", True), ("TEST", "test", True), ("PROD", "live", True),
    ("DEV", "live", False), ("TEST", "live", False), ("PROD", "test", False),
])
def test_stripe_environment_separation(app_config, run_cli, environment, key_mode, accepted):
    app_config["GENERAL"]["Environment"] = environment
    app_config["DATABASE"]["Url"] = "postgresql://user:password@db.example.com/heyhanzi?sslmode=require"
    app_config["EMAIL"] = {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com", "FromName": "HeyHanzi",
                           "SupportUrl": "https://heyhanzi.com", "SesRegion": "us-east-1"}
    app_config["TURNSTILE"] = {"SiteKey": "production-site-fixture", "SecretKey": "production-secret-fixture", "Hostnames": ["heyhanzi.com"]}
    app_config["AWS"] = {"AccessKeyId": "test-access", "SecretAccessKey": "test-secret"}
    app_config["STRIPE"] = {"SecretKey": f"sk_{key_mode}_private", "AnnualPriceId": "price_annual",
                            "PortalConfigurationId": "bpc_portal", "WebhookSecret": "whsec_private"}
    result = run_cli(app_config, "scripts/validate_runtime_config.py")
    assert (result.returncode == 0) == accepted
    assert "private" not in result.stdout + result.stderr


def test_partial_billing_configuration_is_rejected(app_config, run_cli):
    app_config["STRIPE"] = {"SecretKey": "sk_test_private"}
    result = run_cli(app_config, "scripts/validate_runtime_config.py")
    assert result.returncode != 0
    assert "Runtime configuration invalid" in result.stderr
    assert "private" not in result.stdout + result.stderr
