"""The one-shot local migration owner; no migrations in API startup."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.bootstrap import bootstrap_database  # noqa: E402

if __name__ == "__main__":
    bootstrap_database()
