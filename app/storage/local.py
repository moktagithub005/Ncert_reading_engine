from collections.abc import Iterator
from pathlib import Path

from app.config import get_settings
from app.storage.base import Storage


class LocalStorage(Storage):
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_settings().local_storage_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return path

    def put(self, key: str, chunks: Iterator[bytes], content_type: str) -> int:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with path.open("wb") as target:
            for chunk in chunks:
                target.write(chunk)
                total += len(chunk)
        return total

    def open(self, key: str) -> Iterator[bytes]:
        with self._path(key).open("rb") as source:
            while chunk := source.read(1024 * 1024):
                yield chunk

    def url(self, key: str, expires: int = 900) -> str:
        del expires
        return f"file://{self._path(key)}"
