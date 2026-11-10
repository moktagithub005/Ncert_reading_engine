from collections.abc import Iterator
from typing import cast

import boto3

from app.config import get_settings
from app.storage.base import Storage


class S3Storage(Storage):
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    def put(self, key: str, chunks: Iterator[bytes], content_type: str) -> int:
        # Multipart upload avoids materialising large PDFs in memory.
        upload = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ContentType=content_type
        )
        parts, buffer, number, total = [], bytearray(), 1, 0
        try:
            for chunk in chunks:
                total += len(chunk)
                buffer.extend(chunk)
                while len(buffer) >= 8 * 1024 * 1024:
                    part = bytes(buffer[: 8 * 1024 * 1024])
                    del buffer[: 8 * 1024 * 1024]
                    parts.append(
                        self.client.upload_part(
                            Bucket=self.bucket,
                            Key=key,
                            UploadId=upload["UploadId"],
                            PartNumber=number,
                            Body=part,
                        )
                    )
                    number += 1
            if buffer or not parts:
                parts.append(
                    self.client.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        UploadId=upload["UploadId"],
                        PartNumber=number,
                        Body=bytes(buffer),
                    )
                )
            self.client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload["UploadId"],
                MultipartUpload={
                    "Parts": [{"ETag": p["ETag"], "PartNumber": i + 1} for i, p in enumerate(parts)]
                },
            )
        except Exception:
            self.client.abort_multipart_upload(
                Bucket=self.bucket, Key=key, UploadId=upload["UploadId"]
            )
            raise
        return total

    def open(self, key: str) -> Iterator[bytes]:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            while chunk := body.read(1024 * 1024):
                yield chunk
        finally:
            body.close()

    def url(self, key: str, expires: int = 900) -> str:
        return cast(
            str,
            self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
            ),
        )
