"""Compatible command: verify the database provisioned by Compose/Render."""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_loader import env  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.environment import validate_runtime_config  # noqa: E402


async def main():
    validate_runtime_config(env.config)
    database = Database(env.config)
    try:
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        print("Database is accessible.")
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
