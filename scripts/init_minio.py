"""Wait for local MinIO and create the configured development bucket."""

import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

client = boto3.client(
    "s3",
    endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://minio:9000"),
    aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
    aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
    config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}),
)
bucket = os.environ.get("S3_BUCKET", "ncert")

for attempt in range(60):
    try:
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            client.create_bucket(Bucket=bucket)
        print(f"MinIO bucket '{bucket}' is ready")
        break
    except (BotoCoreError, ClientError) as error:
        if attempt == 59:
            raise RuntimeError("MinIO did not become ready") from error
        time.sleep(2)
