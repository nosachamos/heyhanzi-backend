from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_ms() -> int:
    return int(utc_now().timestamp() * 1000)


def utc_now_seconds() -> int:
    return int(utc_now().timestamp())


def format_utc_timestamp(timestamp_seconds: int) -> str:
    return datetime.fromtimestamp(timestamp_seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")
