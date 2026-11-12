import json
import os
import subprocess
import sys

import boto3
import pytest
from botocore.stub import Stubber

from scripts.load_production_config import load_production_config


def test_reads_only_encrypted_heyhanzi_parameter():
    client = boto3.client("ssm", region_name="us-east-1", aws_access_key_id="test",
                          aws_secret_access_key="test")
    document = {"GENERAL": {"Environment": "PROD"}, "DATABASE": {"Url": "secret"}}
    with Stubber(client) as stub:
        stub.add_response("get_parameter", {"Parameter": {
            "Type": "SecureString", "Value": json.dumps(document),
        }}, {"Name": "/HeyHanzi/Env/PROD/Configs", "WithDecryption": True})
        assert load_production_config(client) == document
        stub.assert_no_pending_responses()


def test_plaintext_production_parameter_is_rejected():
    client = boto3.client("ssm", region_name="us-east-1", aws_access_key_id="test",
                          aws_secret_access_key="test")
    with Stubber(client) as stub:
        stub.add_response("get_parameter", {"Parameter": {
            "Type": "String", "Value": '{"GENERAL":{"Environment":"PROD"}}',
        }}, {"Name": "/HeyHanzi/Env/PROD/Configs", "WithDecryption": True})
        with pytest.raises(ValueError, match="encrypted"):
            load_production_config(client)


def test_staging_transfers_live_billing_securely_to_fly(app_config, monkeypatch, tmp_path, capsys):
    app_config["GENERAL"]["Environment"] = "PROD"
    app_config["DATABASE"]["Url"] = "postgresql://user:password@db.example.com/heyhanzi?sslmode=require"
    app_config["EMAIL"] = {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com", "FromName": "HeyHanzi",
                           "SupportUrl": "https://heyhanzi.com", "SesRegion": "us-east-1"}
    app_config["TURNSTILE"] = {"SiteKey": "production-site-fixture", "SecretKey": "production-secret-fixture", "Hostnames": ["heyhanzi.com"]}
    app_config["AWS"] = {"AccessKeyId": "test-access", "SecretAccessKey": "test-secret"}
    app_config["OAUTH"] = {}
    app_config["RENDER"] = {"ApiKey": "management_private"}
    app_config["STRIPE"] = {"SecretKey": "sk_live_fake_private", "AnnualPriceId": "price_annual",
                            "PortalConfigurationId": "bpc_portal", "WebhookSecret": "whsec_fake_private"}
    # Replace the external CLI boundary with an executable receiving the same stdin/argv.
    executable = tmp_path / "flyctl"
    executable.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                          "with open(os.environ['FLY_CAPTURE'], 'w') as stream:\n"
                          " json.dump({'arguments':sys.argv[1:], 'stdin':sys.stdin.read()}, stream)\n")
    executable.chmod(0o700)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FLY_CAPTURE", str(capture))
    monkeypatch.setenv("GITHUB_SHA", "release123")
    monkeypatch.setenv("DEPLOYMENT_TIMESTAMP", "2026-10-14T12:00:00Z")
    # Run a cold deployment process, with no app document or local-file fallback.
    child_environment = {**os.environ, "APP_ENV": "PROD", "HEYHANZI_DISABLE_DOTENV": "true"}
    child_environment.pop("APP_CONFIGS", None)
    result = subprocess.run([sys.executable, "-c", """
import json, sys, boto3
from botocore.stub import Stubber
from scripts.stage_production_config import main
config = json.load(sys.stdin)
client = boto3.client('ssm', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
with Stubber(client) as stub:
    stub.add_response('get_parameter', {'Parameter': {'Type': 'SecureString', 'Value': json.dumps(config)}},
                      {'Name': '/HeyHanzi/Env/PROD/Configs', 'WithDecryption': True})
    main(client)
"""], input=json.dumps(app_config), env=child_environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "private" not in result.stdout + result.stderr
    sent = json.loads(capture.read_text())
    assert sent["arguments"] == ["secrets", "import", "--stage", "--app", "heyhanzi-backend"]
    assert sent["stdin"].startswith("APP_CONFIGS=")
    runtime = json.loads(sent["stdin"].removeprefix("APP_CONFIGS="))
    assert "RENDER" not in runtime
    assert runtime["STRIPE"] == app_config["STRIPE"]
    assert runtime["TURNSTILE"] == app_config["TURNSTILE"]
    assert runtime["GENERAL"]["ReleaseSha"] == "release123"
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
