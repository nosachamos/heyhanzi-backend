import hashlib
import secrets


def create_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def opaque_token_is_expired(expires_at: int, now: int) -> bool:
    return expires_at <= now
