"""Configure or forward Stripe test webhooks using the ignored backend .env."""
import argparse
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from dotenv import dotenv_values, set_key

from scripts.setup_stripe import EVENTS

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure", action="store_true", help="Save the CLI signing secret and exit.")
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".env")
    key = config.get("STRIPE_SECRET_KEY", "") or ""
    if not key.startswith(("sk_test_", "rk_test_")):
        parser.error("The backend .env must contain a Stripe test key.")
    cli = ["stripe"] if shutil.which("stripe") else ["npx", "--yes", "@stripe/cli@1.53.1"]
    environment = {**os.environ, "STRIPE_API_KEY": key, "STRIPE_DEVICE_NAME": "heyhanzi-local"}
    command = [*cli, "listen", "--skip-update", "--events", ",".join(EVENTS)]
    result = subprocess.run([*command, "--print-secret"], env=environment,
                            capture_output=True, text=True, timeout=90)
    match = re.search(r"whsec_[a-zA-Z0-9]+", result.stdout)
    if result.returncode or not match:
        raise SystemExit("Could not retrieve the local Stripe signing secret; check API access.")
    secret = match.group()
    if args.configure:
        os.chmod(ROOT / ".env", 0o600)
        set_key(ROOT / ".env", "STRIPE_WEBHOOK_SECRET", secret)
        print("Local test webhook configured. Run make dev-up to reload the API, then make stripe-listen.")
        return
    if config.get("STRIPE_WEBHOOK_SECRET") != secret:
        raise SystemExit("Run make stripe-configure, then make dev-up before starting the listener.")
    # mkcert's trusted local CA is used; TLS certificate verification stays enabled.
    command += ["--forward-to", "https://local-api.heyhanzi.com:8002/billing/webhook"]
    print("Forwarding Stripe test events to the local HeyHanzi API. Press Ctrl+C to stop.", flush=True)
    with subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, start_new_session=True) as process:
        try:
            for line in process.stdout:
                print(re.sub(r"(?:whsec_|[sr]k_(?:test|live)_)[a-zA-Z0-9]+", "[redacted]", line), end="", flush=True)
            if process.wait():
                raise SystemExit("Stripe listener stopped unexpectedly.")
        except KeyboardInterrupt:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
