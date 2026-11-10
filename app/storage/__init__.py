from app.config import get_settings
from app.storage.base import Storage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


def get_storage() -> Storage:
    return S3Storage() if get_settings().storage_backend == "s3" else LocalStorage()
