import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_loader import env  # noqa: E402
from app.environment import validate_runtime_config  # noqa: E402

if __name__ == "__main__":
    try:
        validate_runtime_config(env.config)
    except Exception:
        print("Runtime configuration invalid; check the documented required fields.", file=sys.stderr)
        raise SystemExit(1) from None
    print("Runtime configuration valid.")
