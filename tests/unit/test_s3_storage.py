from io import BytesIO
from unittest.mock import Mock

import pytest

from app.storage.s3 import S3Storage


class FakeS3Client:
    def __init__(self, fail_upload: bool = False):
        self.fail_upload = fail_upload
        self.parts = []
        self.aborted = False
        self.completed = None

    def create_multipart_upload(self, **_kwargs):
        return {"UploadId": "upload-1"}

    def upload_part(self, **kwargs):
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.parts.append(kwargs)
        return {"ETag": f"etag-{kwargs['PartNumber']}"}

    def complete_multipart_upload(self, **kwargs):
        self.completed = kwargs

    def abort_multipart_upload(self, **_kwargs):
        self.aborted = True

    def get_object(self, **_kwargs):
        return {"Body": BytesIO(b"asset-bytes")}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://storage.test/{operation}/{Params['Key']}?ttl={ExpiresIn}"


def _settings():
    return Mock(
        s3_bucket="ncert",
        s3_endpoint_url="http://minio:9000",
        s3_access_key="local-access",
        s3_secret_key="local-secret",
    )


def test_s3_storage_completes_multipart_and_streams(monkeypatch) -> None:
    import app.storage.s3 as s3_module

    client = FakeS3Client()
    monkeypatch.setattr(s3_module, "get_settings", _settings)
    monkeypatch.setattr(s3_module.boto3, "client", lambda *_args, **_kwargs: client)
    storage = S3Storage()

    size = storage.put("source/book.pdf", iter([b"first", b"second"]), "application/pdf")

    assert size == 11
    assert len(client.parts) == 1
    assert client.completed["MultipartUpload"]["Parts"] == [{"ETag": "etag-1", "PartNumber": 1}]
    assert b"".join(storage.open("assets/book.webp")) == b"asset-bytes"
    assert storage.url("assets/book.webp", expires=60).endswith("ttl=60")


def test_s3_storage_aborts_incomplete_multipart(monkeypatch) -> None:
    import app.storage.s3 as s3_module

    client = FakeS3Client(fail_upload=True)
    monkeypatch.setattr(s3_module, "get_settings", _settings)
    monkeypatch.setattr(s3_module.boto3, "client", lambda *_args, **_kwargs: client)
    storage = S3Storage()

    with pytest.raises(RuntimeError, match="upload failed"):
        storage.put("source/book.pdf", iter([b"bad"]), "application/pdf")

    assert client.aborted
