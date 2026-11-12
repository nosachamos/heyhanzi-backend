"""Provision private, pre-generated sentence media.

Uses the deployment identity. Application credentials stay in SSM/local ignored
config. Application request handling does not use AWS credentials for audio. Does not publish an application release.
"""

import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
PARAMETER = "/HeyHanzi/Env/PROD/Configs"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure-local", action="store_true")
    args = parser.parse_args()
    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"heyhanzi-learning-audio-{account}"
    s3 = boto3.client("s3", region_name=REGION)
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] != "404":
            raise
        s3.create_bucket(Bucket=bucket)
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [
        {"Key": "Project", "Value": "HeyHanzi"}, {"Key": "Purpose", "Value": "LearningAudio"},
    ]})
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
    })
    s3.put_bucket_ownership_controls(Bucket=bucket, OwnershipControls={
        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}],
    })
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [{
        "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
    }]})
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    statements = []
    try:
        statements = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])["Statement"]
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchBucketPolicy":
            raise
    statements = [item for item in statements if item.get("Sid") != "TLSOnly"]
    statements.append({
        "Sid": "TLSOnly", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    })
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": statements}))
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["https://heyhanzi.com", "https://www.heyhanzi.com",
                           "https://local.heyhanzi.com:3002"],
        "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["Range"],
        "ExposeHeaders": ["Content-Length", "Content-Type", "ETag"], "MaxAgeSeconds": 300,
    }]})
    ssm = boto3.client("ssm", region_name=REGION)
    config = json.loads(ssm.get_parameter(Name=PARAMETER, WithDecryption=True)["Parameter"]["Value"])
    app_identity = boto3.client("sts", aws_access_key_id=config["AWS"]["AccessKeyId"],
                               aws_secret_access_key=config["AWS"]["SecretAccessKey"]).get_caller_identity()
    if app_identity["Account"] != account or ":user/" not in app_identity["Arn"]:
        raise ValueError("Expected this account's existing HeyHanzi application IAM user")
    try:
        boto3.client("iam").delete_user_policy(UserName=app_identity["Arn"].split("/")[-1],
                                               PolicyName="HeyHanziLearningAudio")
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchEntity":
            raise
    output = Path(__file__).resolve().parents[1] / "app/data/sentence-audio/storage.json"
    audio_config = json.loads(output.read_text()) if output.exists() else {}
    audio_config.update(Region=REGION, Bucket=bucket)
    config["LEARNING_AUDIO"] = audio_config
    ssm.put_parameter(Name=PARAMETER, Value=json.dumps(config), Type="SecureString", Overwrite=True)
    if args.configure_local:
        file = Path(__file__).resolve().parents[1] / ".configs.json"
        local = json.loads(file.read_text())
        local["LEARNING_AUDIO"] = audio_config
        file.write_text(json.dumps(local, indent=2) + "\n")
    output = Path(__file__).resolve().parents[1] / "app/data/sentence-audio/storage.json"
    output.write_text(json.dumps(audio_config, indent=2) + "\n")
    print(json.dumps(audio_config))  # Infrastructure locations only; no credentials.


if __name__ == "__main__":
    main()
