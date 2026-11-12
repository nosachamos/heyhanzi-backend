from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text

from alembic import context
from app import models  # noqa: F401 -- register every model for autogenerate
from app.config_loader import env
from app.database.database import Base
from app.environment import validate_runtime_config
from app.utils.database_urls import database_url, to_sync_database_url

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
validate_runtime_config(env.config)
url = to_sync_database_url(database_url(env.config, migration=True))


def run_migrations_offline():
    context.configure(
        url=url, target_metadata=Base.metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(
        url, poolclass=pool.NullPool, hide_parameters=True,
        connect_args={"connect_timeout": 10, "application_name": "heyhanzi-migrations"},
    )
    try:
        with engine.connect() as connection:
            # Serialize concurrent release commands and fail instead of waiting forever.
            # Migrations use the direct URL: transaction poolers cannot hold session locks.
            connection.execute(text("SET lock_timeout = '15s'"))
            connection.execute(text("SET statement_timeout = '120s'"))
            connection.execute(text("SELECT pg_advisory_lock(726140091)"))
            connection.commit()
            try:
                context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(726140091)"))
                connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
