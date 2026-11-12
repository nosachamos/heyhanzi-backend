from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from app.config_loader import env
from app.security.email import is_valid_auth_email

DEFAULT_FROM_EMAIL = "no-reply@heyhanzi.com"
DEFAULT_FROM_NAME = "HeyHanzi"
DEFAULT_SUPPORT_URL = "https://heyhanzi.com"

LOCAL_EMAIL_PROVIDERS = {"fake", "console", "smtp_capture"}
SUPPORTED_EMAIL_PROVIDERS = LOCAL_EMAIL_PROVIDERS | {"ses"}
PRODUCTION_ENVIRONMENTS = {"STAGING", "PROD"}


class EmailConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EmailConfig:
    provider: str
    app_base_url: str
    from_email: str
    from_name: str
    support_url: str
    reply_to_email: str | None = None
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    smtp_start_tls: bool = False
    ses_region: str = "us-east-1"
    ses_configuration_set: str | None = None


def _nested(source: dict, *path: str, default=None):
    value = source
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _runtime_name(source: dict) -> str:
    raw = str(_nested(source, "GENERAL", "Environment", default="DEV")).strip().upper()
    if raw in {"PROD", "PRODUCTION"}:
        return "PROD"
    if raw == "STAGING":
        return "STAGING"
    return "DEV" if raw in {"", "DEV", "LOCAL", "DEVELOPMENT"} else raw


def _default_provider(source: dict) -> str:
    return "ses" if _runtime_name(source) in PRODUCTION_ENVIRONMENTS else "smtp_capture"


def _string_value(value, default: str | None = None) -> str | None:
    if value is None:
        return default
    stripped = str(value).strip()
    return stripped or default


def _bool_value(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _int_value(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _origin_from_config(raw_url: str | None) -> str:
    parsed = urlparse(raw_url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EmailConfigError("Email app base URL must be an absolute HTTP(S) origin")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise EmailConfigError("Email app base URL must not include path, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}"


def email_config_from_source(config: dict | None = None) -> EmailConfig:
    source = config if config is not None else env.config
    email_source = source.get("EMAIL", {}) if isinstance(source.get("EMAIL"), dict) else {}
    provider = str(email_source.get("Provider") or _default_provider(source)).strip().lower()
    app_base_url = _origin_from_config(
        _string_value(email_source.get("AppBaseUrl"))
        or _string_value(_nested(source, "URLS", "FrontendUrl"))
    )

    return EmailConfig(
        provider=provider,
        app_base_url=app_base_url,
        from_email=_string_value(email_source.get("FromEmail"), DEFAULT_FROM_EMAIL) or DEFAULT_FROM_EMAIL,
        from_name=_string_value(email_source.get("FromName"), DEFAULT_FROM_NAME) or DEFAULT_FROM_NAME,
        support_url=_string_value(email_source.get("SupportUrl"), DEFAULT_SUPPORT_URL) or DEFAULT_SUPPORT_URL,
        reply_to_email=_string_value(email_source.get("ReplyToEmail")),
        smtp_host=_string_value(email_source.get("SmtpHost"), "mailpit") or "mailpit",
        smtp_port=_int_value(email_source.get("SmtpPort"), 1025),
        smtp_username=_string_value(email_source.get("SmtpUsername")),
        smtp_password=_string_value(email_source.get("SmtpPassword")),
        smtp_use_tls=_bool_value(email_source.get("SmtpUseTls"), False),
        smtp_start_tls=_bool_value(email_source.get("SmtpStartTls"), False),
        ses_region=_string_value(email_source.get("SesRegion"), "us-east-1") or "us-east-1",
        ses_configuration_set=_string_value(email_source.get("SesConfigurationSet")),
    )


def validate_email_config(config: dict | None = None) -> list[str]:
    source = config if config is not None else env.config
    issues: list[str] = []
    try:
        email_config = email_config_from_source(source)
    except EmailConfigError as exc:
        return [str(exc)]

    if email_config.provider not in SUPPORTED_EMAIL_PROVIDERS:
        issues.append(f"Unsupported email provider: {email_config.provider}")

    runtime = _runtime_name(source)
    email_source = source.get("EMAIL", {}) if isinstance(source.get("EMAIL"), dict) else {}

    if runtime == "PROD" and email_config.provider != "ses":
        issues.append("provider must be ses")

    if runtime in PRODUCTION_ENVIRONMENTS:
        if urlparse(email_config.app_base_url).scheme != "https":
            issues.append("app base URL must use https")

    if runtime in PRODUCTION_ENVIRONMENTS and email_config.provider == "ses":
        if not email_config.from_email or not is_valid_auth_email(email_config.from_email):
            issues.append("SES from email must be configured")
        if runtime == "PROD" and not _string_value(email_source.get("FromEmail")):
            issues.append("SES from email must be explicitly configured")
        if runtime == "PROD" and not _string_value(email_source.get("FromName")):
            issues.append("SES from name must be explicitly configured")
        if not email_config.ses_region:
            issues.append("SES region must be configured")
        if runtime == "PROD" and not _string_value(email_source.get("SesRegion")):
            issues.append("SES region must be explicitly configured")
        support_url = urlparse(email_config.support_url)
        if support_url.scheme != "https" or not support_url.netloc:
            issues.append("support URL must be an absolute https URL")
        if runtime == "PROD" and not _string_value(email_source.get("SupportUrl")):
            issues.append("support URL must be explicitly configured")

    if email_config.provider == "smtp_capture":
        if not email_config.smtp_host:
            issues.append("SMTP capture host must be configured")
        if email_config.smtp_port <= 0:
            issues.append("SMTP capture port must be configured")

    return issues


def build_app_url(
    path: str,
    *,
    query: dict[str, str] | None = None,
    config: dict | None = None,
) -> str:
    email_config = email_config_from_source(config)
    normalized_path = f"/{path.lstrip('/')}"
    query_string = f"?{urlencode(query)}" if query else ""
    return f"{email_config.app_base_url}{normalized_path}{query_string}"
