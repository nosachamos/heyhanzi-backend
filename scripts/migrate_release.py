"""Fly's sole release migration owner. Never creates, drops or resets a database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.bootstrap import bootstrap_database  # noqa: E402

if __name__ == "__main__":
    bootstrap_database(release=True)
