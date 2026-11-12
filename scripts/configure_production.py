"""Initialize/update encrypted HeyHanzi configuration without logging credentials."""
import argparse
import base64
import json
import secrets
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.environment import validate_runtime_config  # noqa: E402
from scripts.load_production_config import PARAMETER  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-file", type=Path)
    args = parser.parse_args()
    ssm = boto3.client("ssm", region_name="us-east-1")
    try:
        previous = ssm.get_parameter(Name=PARAMETER, WithDecryption=True)["Parameter"]
        if previous["Type"] != "SecureString":
            raise ValueError("Existing parameter is not encrypted")
        config = json.loads(previous["Value"])
        exists = True
    except ssm.exceptions.ParameterNotFound:
        config = {
            "GENERAL": {"Environment": "PROD"},
            "URLS": {"FrontendUrl": "https://heyhanzi.com", "BackendUrl": "https://api.heyhanzi.com",
                     "AllowedOrigins": ["https://www.heyhanzi.com"]},
            "DATABASE": {"Url": "", "MigrationUrl": "", "DatabaseName": ""},
            "AWS": {"Region": "us-east-1", "AccessKeyId": "", "SecretAccessKey": ""},
        }
        exists = False
    original = json.dumps(config, sort_keys=True)
    auth = config.setdefault("AUTH", {})
    for name in ("SecretKey", "AuditHashSecret", "TokenEncryptionKey"):
        if not auth.get(name):
            auth[name] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    auth.setdefault("EmailPasswordEnabled", True)
    auth.setdefault("EmailSignupEnabled", True)
    config.setdefault("EMAIL", {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com",
                               "FromName": "HeyHanzi", "SupportUrl": "https://heyhanzi.com",
                               "SesRegion": "us-east-1"})
    oauth = config.setdefault("OAUTH", {})
    for name in ("Google", "Github", "LinkedIn"):
        oauth.setdefault(name, {})
    if args.database_url_file:
        from sqlalchemy.engine import make_url
        url = make_url(args.database_url_file.read_text().strip())
        if not url.host or not url.host.endswith(".render.com"):
            raise ValueError("Use the new Render external database URL")
        url = url.update_query_dict({"sslmode": "verify-full"})
        value = url.render_as_string(hide_password=False)
        config["DATABASE"] = {"Url": value, "MigrationUrl": value, "DatabaseName": url.database}
        validate_runtime_config(config)
    if exists and json.dumps(config, sort_keys=True) == original:
        print("HeyHanzi parameter is already configured; left unchanged.")
        return
    ssm.put_parameter(Name=PARAMETER, Type="SecureString", Tier="Standard", Overwrite=exists,
                      Description="HeyHanzi production backend configuration",
                      Value=json.dumps(config, separators=(",", ":")))
    print("HeyHanzi encrypted configuration saved; database " + (
        "configured." if config["DATABASE"]["Url"] else "pending a new Render instance."
    ))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Production configuration failed; no secret values have been logged.", file=sys.stderr)
        raise SystemExit(1) from None
