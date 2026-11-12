"""Provision HeyHanzi SES identity, DKIM DNS and narrowly scoped sending access.

Requires AWS deployment permissions. Does not send email or replace existing DNS.
"""
import json

import boto3

REGION = "us-east-1"
DOMAIN = "heyhanzi.com"
PARAMETER = "/HeyHanzi/Env/PROD/Configs"


def main():
    ssm = boto3.client("ssm", region_name=REGION)
    config = json.loads(ssm.get_parameter(Name=PARAMETER, WithDecryption=True)["Parameter"]["Value"])
    credentials = config["AWS"]
    app_session = boto3.Session(aws_access_key_id=credentials["AccessKeyId"],
                                aws_secret_access_key=credentials["SecretAccessKey"], region_name=REGION)
    principal = app_session.client("sts").get_caller_identity()
    if ":user/" not in principal["Arn"]:
        raise ValueError("Application sending access must be configured for its IAM principal")
    ses = boto3.client("sesv2", region_name=REGION)
    try:
        identity = ses.get_email_identity(EmailIdentity=DOMAIN)
    except ses.exceptions.NotFoundException:
        ses.create_email_identity(EmailIdentity=DOMAIN,
                                  DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"})
        identity = ses.get_email_identity(EmailIdentity=DOMAIN)
    route53 = boto3.client("route53")
    zones = [zone for zone in route53.list_hosted_zones_by_name(DNSName=DOMAIN)["HostedZones"]
             if zone["Name"] == DOMAIN + "." and not zone["Config"]["PrivateZone"]]
    if len(zones) != 1:
        raise ValueError("Expected exactly one HeyHanzi public hosted zone")
    zone_id = zones[0]["Id"]
    records = {(row["Name"], row["Type"]): row for page in route53.get_paginator("list_resource_record_sets").paginate(HostedZoneId=zone_id)
               for row in page["ResourceRecordSets"]}
    planned = [{"Name": f"{token}._domainkey.{DOMAIN}.", "Type": "CNAME", "TTL": 300,
                "ResourceRecords": [{"Value": f"{token}.dkim.amazonses.com"}]}
               for token in identity["DkimAttributes"]["Tokens"]]
    changes = []
    for record in planned:
        existing = records.get((record["Name"], record["Type"]))
        if existing:
            if [x["Value"].rstrip(".") for x in existing["ResourceRecords"]] != [x["Value"].rstrip(".") for x in record["ResourceRecords"]]:
                raise ValueError("Existing DNS record differs; refusing to overwrite it")
        else:
            changes.append({"Action": "CREATE", "ResourceRecordSet": record})
    if changes:
        result = route53.change_resource_record_sets(HostedZoneId=zone_id,
            ChangeBatch={"Comment": "HeyHanzi transactional email DKIM", "Changes": changes})
        print("SES DKIM records submitted:", result["ChangeInfo"]["Id"])
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["ses:SendEmail"],
               "Resource": f"arn:aws:ses:{REGION}:{principal['Account']}:identity/{DOMAIN}",
               "Condition": {"StringEquals": {"ses:FromAddress": "no-reply@heyhanzi.com"}}}]}
    boto3.client("iam").put_user_policy(UserName=principal["Arn"].split("/")[-1],
        PolicyName="HeyHanziTransactionalEmail", PolicyDocument=json.dumps(policy))
    print("HeyHanzi sender permission configured; SES verification status:", identity["VerificationStatus"])


if __name__ == "__main__":
    main()
