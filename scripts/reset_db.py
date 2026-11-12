"""Reset only this Compose project's disposable local development database."""
import argparse
import json
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != "RESET-DEV-heyhanzi-dev":
        parser.error("Requires --confirm RESET-DEV-heyhanzi-dev")
    # No configurable connection URL: this command can only address our Compose service.
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        check=True, capture_output=True, text=True,
    )
    config = json.loads(result.stdout)
    service = config["services"]["postgres"]
    if config["name"] != "heyhanzi-backend" or service["environment"]["POSTGRES_DB"] != "heyhanzi-dev":
        parser.error("Only the heyhanzi-backend local Compose database may be reset")
    subprocess.run(["docker", "compose", "stop", "backend"], check=True)
    subprocess.run([
        "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "postgres",
        "-v", "ON_ERROR_STOP=1", "-c", 'DROP DATABASE "heyhanzi-dev" WITH (FORCE)',
        "-c", 'CREATE DATABASE "heyhanzi-dev"',
    ], check=True)
    subprocess.run(["docker", "compose", "run", "--rm", "database-bootstrap"], check=True)
    subprocess.run(["docker", "compose", "up", "-d", "backend"], check=True)


if __name__ == "__main__":
    main()
