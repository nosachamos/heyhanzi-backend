"""APP_CONFIGS -> .configs.json -> environment override convention."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


class ConfigLoader:
    def __init__(self):
        # A supplied production document must never be contaminated by local .env.
        if not os.getenv("APP_CONFIGS") and os.getenv("APP_ENV", "DEV") == "DEV":
            if os.getenv("HEYHANZI_DISABLE_DOTENV", "").lower() not in {"1", "true"}:
                load_dotenv(ROOT / ".env", override=False)
        source = os.getenv("APP_CONFIGS")
        if source:
            try:
                self.config = json.loads(source)
            except (ValueError, TypeError):
                raise ValueError("APP_CONFIGS must be valid JSON") from None
            if not isinstance(self.config, dict):
                raise ValueError("APP_CONFIGS must be an object")
        elif os.getenv("APP_ENV", "DEV") != "DEV":
            raise ValueError("APP_CONFIGS is required outside development")
        else:
            self.config = json.loads((ROOT / ".configs.json").read_text())
        self.apply_env_overrides()

    def apply_env_overrides(self):
        overrides = {
            "TURNSTILE_SITE_KEY": ("TURNSTILE", "SiteKey"),
            "TURNSTILE_SECRET": ("TURNSTILE", "SecretKey"),
            "TURNSTILE_ENABLED": ("TURNSTILE", "Enabled"),
            "STRIPE_SECRET_KEY": ("STRIPE", "SecretKey"),
            "STRIPE_ANNUAL_PRICE_ID": ("STRIPE", "AnnualPriceId"),
            "STRIPE_WEBHOOK_SECRET": ("STRIPE", "WebhookSecret"),
            "STRIPE_PORTAL_CONFIGURATION_ID": ("STRIPE", "PortalConfigurationId"),
            "APP_ENV": ("GENERAL", "Environment"),
            "DEPLOYMENT_TIMESTAMP": ("GENERAL", "DeploymentTimestamp"),
            "RELEASE_SHA": ("GENERAL", "ReleaseSha"),
            "DATABASE_URL": ("DATABASE", "Url"),
            "MIGRATION_DATABASE_URL": ("DATABASE", "MigrationUrl"),
            "DATABASE_NAME": ("DATABASE", "DatabaseName"),
            "APP_BASE_URL": ("URLS", "FrontendUrl"),
            "BACKEND_URL": ("URLS", "BackendUrl"),
            "AUTH_SECRET_KEY": ("AUTH", "SecretKey"),
            "AUTH_AUDIT_HASH_SECRET": ("AUTH", "AuditHashSecret"),
            "AUTH_TOKEN_ENCRYPTION_KEY": ("AUTH", "TokenEncryptionKey"),
            "EMAIL_PROVIDER": ("EMAIL", "Provider"),
            "SMTP_HOST": ("EMAIL", "SmtpHost"),
            "SMTP_PORT": ("EMAIL", "SmtpPort"),
            "EMAIL_FROM_EMAIL": ("EMAIL", "FromEmail"),
            "EMAIL_FROM_NAME": ("EMAIL", "FromName"),
            "SES_REGION": ("EMAIL", "SesRegion"),
            "AWS_ACCESS_KEY_ID": ("AWS", "AccessKeyId"),
            "AWS_SECRET_ACCESS_KEY": ("AWS", "SecretAccessKey"),
        }
        for variable, (section, field) in overrides.items():
            if variable in os.environ:
                self.config.setdefault(section, {})[field] = os.environ[variable]
        for name, section in (("GOOGLE", "Google"), ("GITHUB", "Github"), ("LINKEDIN", "LinkedIn")):
            for suffix, field in (("CLIENT_ID", "ClientId"), ("CLIENT_SECRET", "ClientSecret")):
                value = os.getenv(f"{name}_{suffix}")
                if value is not None:
                    self.config.setdefault("OAUTH", {}).setdefault(section, {})[field] = value
        if "TURNSTILE_HOSTNAMES" in os.environ:
            self.config.setdefault("TURNSTILE", {})["Hostnames"] = [
                host.strip() for host in os.environ["TURNSTILE_HOSTNAMES"].split(",") if host.strip()
            ]
        if "ADMIN_EMAILS" in os.environ:
            self.config.setdefault("ADMIN", {})["Emails"] = [
                value.strip().casefold() for value in os.environ["ADMIN_EMAILS"].split(",") if value.strip()
            ]
        if "CORS_ORIGINS" in os.environ:
            self.config.setdefault("URLS", {})["AllowedOrigins"] = [
                value.strip() for value in os.environ["CORS_ORIGINS"].split(",") if value.strip()
            ]

    def __getitem__(self, key):
        return self.config.get(key, {})


env = ConfigLoader()
