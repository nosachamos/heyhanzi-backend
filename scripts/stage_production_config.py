"""Validate SSM configuration and stage it on Fly without shell arguments/logs."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.environment import validate_runtime_config  # noqa: E402
from scripts.load_production_config import load_production_config  # noqa: E402


def main(ssm_client=None):
    config = load_production_config(ssm_client)
    validate_runtime_config(config)
    if not config.get("STRIPE", {}).get("SecretKey"):
        raise ValueError("Production billing configuration is required")
    config["GENERAL"]["ReleaseSha"] = os.environ["GITHUB_SHA"]
    config["GENERAL"]["DeploymentTimestamp"] = os.environ["DEPLOYMENT_TIMESTAMP"]
    # Runtime SES credentials are explicit; no ambient/default AWS identity fallback.
    runtime = {key: config[key] for key in ("GENERAL", "URLS", "DATABASE", "AUTH", "EMAIL", "OAUTH", "AWS", "STRIPE", "TURNSTILE")}
    payload = "APP_CONFIGS=" + json.dumps(runtime, separators=(",", ":")) + "\n"
    subprocess.run(
        ["flyctl", "secrets", "import", "--stage", "--app", "heyhanzi-backend"],
        input=payload, text=True, capture_output=True, check=True,
    )
    print("Validated production config staged on heyhanzi-backend.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Production config staging failed; verify SSM, Render config and Fly access.", file=sys.stderr)
        raise SystemExit(1) from None
