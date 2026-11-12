import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.utils.database_urls import database_url, to_async_database_url


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Database:
    def __init__(self, config: dict):
        migration_config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        self.required_revisions = set(ScriptDirectory.from_config(migration_config).get_heads())
        pool_size = int(os.getenv("HEYHANZI_DB_POOL_SIZE", "2"))
        overflow = int(os.getenv("HEYHANZI_DB_MAX_OVERFLOW", "1"))
        if pool_size < 1 or overflow < 0:
            raise ValueError("Database pool must be bounded with positive pool size")
        self.engine = create_async_engine(
            to_async_database_url(database_url(config)),
            pool_size=pool_size,
            max_overflow=overflow,
            pool_timeout=3,
            pool_recycle=300,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={
                "timeout": 3,
                "command_timeout": 5,
                "server_settings": {
                    "application_name": "heyhanzi-backend",
                    "statement_timeout": "5000",
                    "idle_in_transaction_session_timeout": "10000",
                },
            },
        )
        self.SessionLocal = async_sessionmaker(self.engine, expire_on_commit=False)

    async def verify_ready(self) -> None:
        # Bound total time, including pool checkout and reconnect.
        async with asyncio.timeout(4):
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                # An unbootstrapped database must not pass the load balancer check.
                revisions = await connection.scalars(text("SELECT version_num FROM alembic_version"))
                if set(revisions) != self.required_revisions:
                    raise RuntimeError("Database migrations do not match this release")

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    # The service/route explicitly commits its transaction. Never replay requests.
    async with request.app.state.database.SessionLocal() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
