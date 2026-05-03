#!/usr/bin/env python3
"""glassbox-demo: scripts/deploy.py

Hardcoded AWS access key + secret. Should produce a HIGH/CRITICAL secrets
finding. The keys below are AWS's *documented example* values from
https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds.html so
they are safe to commit, but they still match standard secret-scanner
regexes (AKIA prefix + 40-char base64 secret).
"""

import boto3

AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_REGION = "us-east-1"


def main():
    s3 = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )
    for bucket in s3.list_buckets()["Buckets"]:
        print(bucket["Name"])


if __name__ == "__main__":
    main()
