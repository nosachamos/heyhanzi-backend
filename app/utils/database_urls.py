"""Normalize provider URLs without rewriting the provider-assigned database name."""

from sqlalchemy.engine import URL, make_url


def database_url(config: dict, *, migration: bool = False) -> URL:
    database = config.get("DATABASE", {})
    value = (database.get("MigrationUrl") if migration else None) or database.get("Url")
    try:
        url = make_url(value)
    except Exception:
        raise ValueError("DATABASE.Url must be a PostgreSQL URL") from None
    if url.drivername not in {"postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2"}:
        raise ValueError("Only PostgreSQL is supported")
    if not url.host or not url.database or not url.username:
        raise ValueError("Database URL requires host, database and username")
    name = database.get("DatabaseName")
    if name and name != url.database:
        raise ValueError("DATABASE.DatabaseName must match the database in the URL")
    return url


def to_async_database_url(url: URL) -> URL:
    query = dict(url.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    query.pop("channel_binding", None)
    return url.set(drivername="postgresql+asyncpg", query=query)


def to_sync_database_url(url: URL) -> URL:
    query = dict(url.query)
    if "ssl" in query:
        query["sslmode"] = query.pop("ssl")
    return url.set(drivername="postgresql+psycopg2", query=query)
