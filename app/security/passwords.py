from __future__ import annotations

import re
import string
from dataclasses import dataclass

from argon2 import PasswordHasher, exceptions
from argon2.low_level import Type

from app.config_loader import env
from app.security.email import normalize_email

PASSWORD_HASH_ALGORITHM = "argon2id"
PASSWORD_HASH_VERSION = 1
DEFAULT_PASSWORD_MIN_LENGTH = 12
DEFAULT_PASSWORD_MAX_LENGTH = 128
DEFAULT_ARGON2_TIME_COST = 3
DEFAULT_ARGON2_MEMORY_COST_KIB = 65_536
DEFAULT_ARGON2_PARALLELISM = 4
DEFAULT_ARGON2_HASH_LEN = 32
DEFAULT_ARGON2_SALT_LEN = 16

PASSWORD_TOO_SHORT = "too_short"
PASSWORD_TOO_LONG = "too_long"
PASSWORD_COMMON = "common_password"
PASSWORD_CONTAINS_PERSONAL_INFO = "contains_personal_info"
PASSWORD_CONTAINS_SERVICE_TERM = "contains_service_term"
PASSWORD_COMPOSITION_REQUIRED = "composition_required"

COMMON_PASSWORDS = {
    "000000000000",
    "111111111111",
    "123123123123",
    "123456789012",
    "1234567890ab",
    "1q2w3e4r5t6y",
    "adminadmin12",
    "changeme1234",
    "correcthorsebatterystaple",
    "dragondragon",
    "football1234",
    "iloveyou1234",
    "letmein12345",
    "monkeymonkey",
    "heyhanziheyhanzi1234",
    "passw0rd1234",
    "password",
    "password1",
    "password12",
    "password123",
    "password1234",
    "password12345",
    "passwordpassword",
    "qwerty123456",
    "qwertyqwerty",
    "trustno1trustno1",
    "welcome12345",
    "welcomewelcome",
}

SERVICE_TERMS = {
    "heyhanzi",
    "heyhanzi.com",
    "heyhanzime",
    "hey hanzi",
    "hey-hanzi",
}


@dataclass(frozen=True)
class PasswordPolicy:
    min_length: int = DEFAULT_PASSWORD_MIN_LENGTH
    max_length: int = DEFAULT_PASSWORD_MAX_LENGTH
    require_legacy_composition: bool = False


@dataclass(frozen=True)
class PasswordValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class PasswordValidationResult:
    valid: bool
    issues: tuple[PasswordValidationIssue, ...]


@dataclass(frozen=True)
class PasswordHashParameters:
    time_cost: int = DEFAULT_ARGON2_TIME_COST
    memory_cost: int = DEFAULT_ARGON2_MEMORY_COST_KIB
    parallelism: int = DEFAULT_ARGON2_PARALLELISM
    hash_len: int = DEFAULT_ARGON2_HASH_LEN
    salt_len: int = DEFAULT_ARGON2_SALT_LEN


@dataclass(frozen=True)
class PasswordHashResult:
    encoded_hash: str
    algorithm: str = PASSWORD_HASH_ALGORITHM
    version: int = PASSWORD_HASH_VERSION


def _int_from_config(source: dict, path: tuple[str, ...], default: int) -> int:
    value = source
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    return parsed if parsed > 0 else default


def _bool_from_config(source: dict, path: tuple[str, ...], default: bool) -> bool:
    value = source
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def password_policy(config: dict | None = None) -> PasswordPolicy:
    source = config if config is not None else env.config
    min_length = _int_from_config(source, ("AUTH", "PasswordMinLength"), DEFAULT_PASSWORD_MIN_LENGTH)
    max_length = _int_from_config(source, ("AUTH", "PasswordMaxLength"), DEFAULT_PASSWORD_MAX_LENGTH)
    if max_length < min_length:
        max_length = min_length

    return PasswordPolicy(
        min_length=min_length,
        max_length=max_length,
        require_legacy_composition=_bool_from_config(
            source,
            ("AUTH", "RequirePasswordComposition"),
            False,
        ),
    )


def password_hash_parameters(config: dict | None = None) -> PasswordHashParameters:
    source = config if config is not None else env.config
    return PasswordHashParameters(
        time_cost=_int_from_config(source, ("AUTH", "PasswordHashTimeCost"), DEFAULT_ARGON2_TIME_COST),
        memory_cost=_int_from_config(source, ("AUTH", "PasswordHashMemoryCostKiB"), DEFAULT_ARGON2_MEMORY_COST_KIB),
        parallelism=_int_from_config(source, ("AUTH", "PasswordHashParallelism"), DEFAULT_ARGON2_PARALLELISM),
        hash_len=_int_from_config(source, ("AUTH", "PasswordHashLength"), DEFAULT_ARGON2_HASH_LEN),
        salt_len=_int_from_config(source, ("AUTH", "PasswordHashSaltLength"), DEFAULT_ARGON2_SALT_LEN),
    )


def _password_hasher(parameters: PasswordHashParameters) -> PasswordHasher:
    return PasswordHasher(
        time_cost=parameters.time_cost,
        memory_cost=parameters.memory_cost,
        parallelism=parameters.parallelism,
        hash_len=parameters.hash_len,
        salt_len=parameters.salt_len,
        type=Type.ID,
    )


def _compact(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _contains_term(password: str, term: str) -> bool:
    normalized_password = password.casefold()
    normalized_term = term.casefold()
    compact_password = _compact(password)
    compact_term = _compact(term)

    return (
        len(normalized_term) >= 3
        and normalized_term in normalized_password
    ) or (
        len(compact_term) >= 3
        and compact_term in compact_password
    )


def _personal_terms(email: str | None, display_name: str | None) -> set[str]:
    terms: set[str] = set()
    normalized_email = normalize_email(email)
    if normalized_email:
        terms.add(normalized_email)
        local_part = normalized_email.split("@", 1)[0]
        if len(_compact(local_part)) >= 3:
            terms.add(local_part)

    if display_name:
        stripped_display_name = display_name.strip()
        if len(_compact(stripped_display_name)) >= 3:
            terms.add(stripped_display_name)
        terms.update(
            token
            for token in re.split(r"[\s._@+-]+", stripped_display_name)
            if len(_compact(token)) >= 3
        )

    return terms


def _has_legacy_composition(password: str) -> bool:
    categories = 0
    categories += any(character.islower() for character in password)
    categories += any(character.isupper() for character in password)
    categories += any(character.isdigit() for character in password)
    categories += any(character in string.punctuation or character.isspace() for character in password)
    return categories >= 3


def validate_password(
    password: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
    policy: PasswordPolicy | None = None,
) -> PasswordValidationResult:
    effective_policy = policy or password_policy()
    issues: list[PasswordValidationIssue] = []

    if len(password) < effective_policy.min_length:
        issues.append(PasswordValidationIssue(
            code=PASSWORD_TOO_SHORT,
            message=f"Use at least {effective_policy.min_length} characters.",
        ))

    if len(password) > effective_policy.max_length:
        issues.append(PasswordValidationIssue(
            code=PASSWORD_TOO_LONG,
            message=f"Use {effective_policy.max_length} characters or fewer.",
        ))
        return PasswordValidationResult(valid=False, issues=tuple(issues))

    if password.casefold() in COMMON_PASSWORDS or _compact(password) in COMMON_PASSWORDS:
        issues.append(PasswordValidationIssue(
            code=PASSWORD_COMMON,
            message="Avoid common passwords.",
        ))

    if any(_contains_term(password, term) for term in SERVICE_TERMS):
        issues.append(PasswordValidationIssue(
            code=PASSWORD_CONTAINS_SERVICE_TERM,
            message="Avoid words related to HeyHanzi.",
        ))

    if any(_contains_term(password, term) for term in _personal_terms(email, display_name)):
        issues.append(PasswordValidationIssue(
            code=PASSWORD_CONTAINS_PERSONAL_INFO,
            message="Avoid words related to your email or name.",
        ))

    if effective_policy.require_legacy_composition and not _has_legacy_composition(password):
        issues.append(PasswordValidationIssue(
            code=PASSWORD_COMPOSITION_REQUIRED,
            message="Use at least three character types.",
        ))

    return PasswordValidationResult(valid=not issues, issues=tuple(issues))


def hash_password(password: str, *, config: dict | None = None) -> PasswordHashResult:
    encoded_hash = _password_hasher(password_hash_parameters(config)).hash(password)
    return PasswordHashResult(encoded_hash=encoded_hash)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return PasswordHasher().verify(encoded_hash, password)
    except (exceptions.VerificationError, exceptions.InvalidHashError, TypeError):
        return False


def needs_rehash(encoded_hash: str, *, config: dict | None = None) -> bool:
    try:
        return _password_hasher(password_hash_parameters(config)).check_needs_rehash(encoded_hash)
    except (exceptions.InvalidHashError, TypeError):
        return True
