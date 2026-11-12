from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from app.email.config import validate_email_config


def validate_auth_config(config: dict) -> None:
    backend = urlsplit(config.get("URLS", {}).get("BackendUrl", ""))
    if backend.scheme != "https" or not backend.hostname or backend.path or backend.query or backend.fragment or backend.username:
        raise ValueError("URLS.BackendUrl must be an exact HTTPS origin")
    auth = config.get("AUTH", {})
    if len(auth.get("SecretKey", "")) < 32 or len(auth.get("AuditHashSecret", "")) < 32:
        raise ValueError("AUTH.SecretKey and AuditHashSecret require at least 32 random characters")
    try:
        Fernet(auth.get("TokenEncryptionKey", "").encode())
    except Exception:
        raise ValueError("AUTH.TokenEncryptionKey must be a Fernet key") from None
    issues = validate_email_config(config)
    if issues:
        raise ValueError("Email configuration invalid: " + "; ".join(issues))
    if config["GENERAL"]["Environment"] == "PROD":
        if config.get("EMAIL", {}).get("Provider") != "ses":
            raise ValueError("Production email requires SES")
        aws = config.get("AWS", {})
        if not aws.get("AccessKeyId") or not aws.get("SecretAccessKey"):
            raise ValueError("SES requires explicit AWS credentials")
    for provider in ("Google", "Github", "LinkedIn"):
        values = config.get("OAUTH", {}).get(provider, {})
        if bool(values.get("ClientId")) != bool(values.get("ClientSecret")):
            raise ValueError(f"OAUTH.{provider} needs both ClientId and ClientSecret")


def flag(config: dict, key: str, default: bool = True) -> bool:
    value = config.get("AUTH", {}).get(key, default)
    return value is True or str(value).lower() == "true"
