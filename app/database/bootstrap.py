from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config_loader import env
from app.environment import validate_runtime_config


def bootstrap_database(*, release: bool = False) -> None:
    validate_runtime_config(env.config)
    if release and env.config["GENERAL"]["Environment"] != "PROD":
        raise ValueError("Release migrations require production configuration")
    root = Path(__file__).resolve().parents[2]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
