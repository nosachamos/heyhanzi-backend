"""Read encrypted HeyHanzi config. Stdout is secret-bearing; capture, never log it."""
import json
import sys

import boto3

PARAMETER = "/HeyHanzi/Env/PROD/Configs"


def load_production_config(client=None):
    client = client if client is not None else boto3.client("ssm", region_name="us-east-1")
    parameter = client.get_parameter(Name=PARAMETER, WithDecryption=True)["Parameter"]
    if parameter.get("Type") != "SecureString":
        raise ValueError("Production configuration must be encrypted")
    config = json.loads(parameter["Value"])
    if not isinstance(config, dict) or config.get("GENERAL", {}).get("Environment") != "PROD":
        raise ValueError("Production configuration object required")
    return config


if __name__ == "__main__":
    try:
        result = load_production_config()
    except Exception:
        print("production_configuration_load_failed", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
