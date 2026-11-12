"""Configure a dedicated audio CDN with an authenticated, private S3 origin.

This does not change the website distribution or publish application code.
"""

import json
from pathlib import Path

import boto3


def main():
    directory = Path(__file__).resolve().parents[1] / "app/data/sentence-audio"
    file = directory / "storage.json"
    storage = json.loads(file.read_text())
    cf = boto3.client("cloudfront")
    name = "heyhanzi-learning-audio"
    controls = cf.list_origin_access_controls().get("OriginAccessControlList", {}).get("Items", [])
    control = next((item for item in controls if item["Name"] == name), None)
    if control is None:
        control = cf.create_origin_access_control(OriginAccessControlConfig={
            "Name": name, "Description": "Private HeyHanzi pre-generated audio",
            "SigningProtocol": "sigv4", "SigningBehavior": "always",
            "OriginAccessControlOriginType": "s3",
        })["OriginAccessControl"]
    policies = cf.list_response_headers_policies(Type="custom").get("ResponseHeadersPolicyList", {}).get("Items", [])
    headers = next((item["ResponseHeadersPolicy"] for item in policies
                    if item["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"]["Name"] == name), None)
    headers_config = {
            "Name": name,
            "CorsConfig": {
                "AccessControlAllowCredentials": False,
                "AccessControlAllowOrigins": {"Quantity": 3, "Items": [
                    "https://heyhanzi.com", "https://www.heyhanzi.com", "https://local.heyhanzi.com:3002",
                ]},
                "AccessControlAllowHeaders": {"Quantity": 1, "Items": ["Range"]},
                "AccessControlAllowMethods": {"Quantity": 3, "Items": ["GET", "HEAD", "OPTIONS"]},
                "AccessControlExposeHeaders": {"Quantity": 3, "Items": ["Content-Length", "Content-Type", "ETag"]},
                "AccessControlMaxAgeSec": 300, "OriginOverride": True,
            },
            "SecurityHeadersConfig": {"ContentTypeOptions": {"Override": True}},
        "CustomHeadersConfig": {"Quantity": 1, "Items": [{
            "Header": "Link", "Value": '</licenses/MANDARIN-DATA-NOTICE.txt>; rel="describedby", </licenses/CC-BY-SA-4.0.txt>; rel="license"',
            "Override": True,
        }]},
    }
    if headers is None:
        headers = cf.create_response_headers_policy(ResponseHeadersPolicyConfig=headers_config)["ResponseHeadersPolicy"]
    else:
        current = cf.get_response_headers_policy(Id=headers["Id"])
        headers = cf.update_response_headers_policy(Id=headers["Id"], IfMatch=current["ETag"],
            ResponseHeadersPolicyConfig=headers_config)["ResponseHeadersPolicy"]
    policies = cf.list_cache_policies(Type="custom").get("CachePolicyList", {}).get("Items", [])
    cache = next((item["CachePolicy"] for item in policies
                  if item["CachePolicy"]["CachePolicyConfig"]["Name"] == name), None)
    if cache is None:
        cache = cf.create_cache_policy(CachePolicyConfig={
            "Name": name, "MinTTL": 31536000, "DefaultTTL": 31536000, "MaxTTL": 31536000,
            "ParametersInCacheKeyAndForwardedToOrigin": {
                "EnableAcceptEncodingGzip": False, "EnableAcceptEncodingBrotli": False,
                "HeadersConfig": {"HeaderBehavior": "none"},
                "CookiesConfig": {"CookieBehavior": "none"},
                "QueryStringsConfig": {"QueryStringBehavior": "none"},
            },
        })["CachePolicy"]
    distributions = [item for page in cf.get_paginator("list_distributions").paginate()
                     for item in page.get("DistributionList", {}).get("Items", [])]
    distribution = next((item for item in distributions if item.get("Comment") == name), None)
    if distribution is None:
        distribution = cf.create_distribution(DistributionConfig={
            "CallerReference": name, "Comment": name, "Enabled": True,
            "Origins": {"Quantity": 1, "Items": [{
                "Id": "Audio", "DomainName": f"{storage['Bucket']}.s3.{storage['Region']}.amazonaws.com",
                "OriginAccessControlId": control["Id"], "S3OriginConfig": {"OriginAccessIdentity": ""},
            }]},
            "DefaultCacheBehavior": {
                "TargetOriginId": "Audio", "ViewerProtocolPolicy": "https-only", "Compress": False,
                "AllowedMethods": {"Quantity": 3, "Items": ["GET", "HEAD", "OPTIONS"],
                                   "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
                "TrustedSigners": {"Enabled": False, "Quantity": 0},
                "CachePolicyId": cache["Id"], "ResponseHeadersPolicyId": headers["Id"],
            },
            "CacheBehaviors": {"Quantity": 1, "Items": [{
                "PathPattern": "licenses/*", "TargetOriginId": "Audio",
                "ViewerProtocolPolicy": "https-only", "Compress": True,
                "AllowedMethods": {"Quantity": 3, "Items": ["GET", "HEAD", "OPTIONS"],
                                   "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
                "TrustedSigners": {"Enabled": False, "Quantity": 0},
                "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
                "ResponseHeadersPolicyId": headers["Id"],
            }]},
            "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
            "PriceClass": "PriceClass_All", "HttpVersion": "http2and3", "IsIPV6Enabled": True,
            "CustomErrorResponses": {"Quantity": 2, "Items": [
                {"ErrorCode": code, "ErrorCachingMinTTL": 0} for code in [403, 404]
            ]},
        })["Distribution"]
    elif not distribution.get("CacheBehaviors", {}).get("Quantity"):
        current = cf.get_distribution_config(Id=distribution["Id"])
        config = current["DistributionConfig"]
        behavior = dict(config["DefaultCacheBehavior"])
        behavior.update(PathPattern="licenses/*", Compress=True,
                        CachePolicyId="658327ea-f89d-4fab-a63d-7e88639e58f6")
        config["CacheBehaviors"] = {"Quantity": 1, "Items": [behavior]}
        cf.update_distribution(Id=distribution["Id"], IfMatch=current["ETag"], DistributionConfig=config)
    s3 = boto3.client("s3", region_name=storage["Region"])
    s3.put_bucket_policy(Bucket=storage["Bucket"], Policy=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Sid": "TLSOnly", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
             "Resource": [f"arn:aws:s3:::{storage['Bucket']}", f"arn:aws:s3:::{storage['Bucket']}/*"],
             "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
            {"Sid": "AudioCDN", "Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"},
             "Action": "s3:GetObject", "Resource": [
                 f"arn:aws:s3:::{storage['Bucket']}/sentences/*.mp3",
                 f"arn:aws:s3:::{storage['Bucket']}/licenses/MANDARIN-DATA-NOTICE.txt",
                 f"arn:aws:s3:::{storage['Bucket']}/licenses/CC-BY-SA-4.0.txt",
             ],
             "Condition": {"StringEquals": {"AWS:SourceArn": distribution["ARN"]}}},
        ],
    }))
    storage.update(CdnDomain=distribution["DomainName"], DistributionId=distribution["Id"])
    file.write_text(json.dumps(storage, indent=2) + "\n")
    ssm = boto3.client("ssm", region_name=storage["Region"])
    parameter = "/HeyHanzi/Env/PROD/Configs"
    config = json.loads(ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"])
    config["LEARNING_AUDIO"] = storage
    ssm.put_parameter(Name=parameter, Value=json.dumps(config), Type="SecureString", Overwrite=True)
    print(json.dumps(storage))


if __name__ == "__main__":
    main()
