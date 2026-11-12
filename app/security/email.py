from email_validator import EmailNotValidError, validate_email


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_valid_auth_email(email: str | None) -> bool:
    try:
        validate_email(normalize_email(email), check_deliverability=False)
        return len(email or "") <= 254
    except EmailNotValidError:
        return False
