"""Validate configuration before opening connections or accepting requests."""

from urllib.parse import urlsplit

from app.utils.database_urls import database_url


def is_development(config: dict) -> bool:
    return config.get("GENERAL", {}).get("Environment") == "DEV"


def allowed_origins(config: dict) -> list[str]:
    urls = config.get("URLS", {})
    extra = urls.get("AllowedOrigins", [])
    if not isinstance(extra, list) or not all(isinstance(item, str) for item in extra):
        raise ValueError("URLS.AllowedOrigins must be a list of origins")
    return sorted(set([urls.get("FrontendUrl", ""), *extra]))


def validate_runtime_config(config: dict) -> None:
    if config.get("GENERAL", {}).get("Environment") not in {"DEV", "TEST", "PROD"}:
        raise ValueError("GENERAL.Environment must be DEV, TEST or PROD")
    for origin in allowed_origins(config):
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError("CORS origins must be exact origins without paths or wildcards")
        if config["GENERAL"]["Environment"] == "PROD" and parsed.scheme != "https":
            raise ValueError("Production frontend origins must use HTTPS")
    for migration in (False, True):
        url = database_url(config, migration=migration)
        if config["GENERAL"]["Environment"] == "PROD":
            if url.host in {"localhost", "127.0.0.1", "::1", "postgres"}:
                raise ValueError("Production requires a hosted PostgreSQL database")
            if (url.query.get("sslmode") or url.query.get("ssl")) not in {
                "require", "verify-ca", "verify-full"
            }:
                raise ValueError("Production PostgreSQL connections must require TLS")
    from app.auth.config import validate_auth_config
    validate_auth_config(config)
    from app.security.turnstile import validate_turnstile_config
    validate_turnstile_config(config)
    stripe = config.get("STRIPE", {})
    if any(stripe.values()):
        mode = "live" if config["GENERAL"]["Environment"] == "PROD" else "test"
        if not str(stripe.get("SecretKey", "")).startswith((f"sk_{mode}_", f"rk_{mode}_")):
            raise ValueError(f"Stripe requires a {mode} key for this environment")
        for field, prefix in (("AnnualPriceId", "price_"), ("PortalConfigurationId", "bpc_"),
                              ("WebhookSecret", "whsec_")):
            if not str(stripe.get(field, "")).startswith(prefix):
                raise ValueError(f"STRIPE.{field} is required when billing is configured")
