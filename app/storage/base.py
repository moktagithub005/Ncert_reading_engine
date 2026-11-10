from abc import ABC, abstractmethod
from collections.abc import Iterator


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, chunks: Iterator[bytes], content_type: str) -> int: ...

    @abstractmethod
    def open(self, key: str) -> Iterator[bytes]: ...

    @abstractmethod
    def url(self, key: str, expires: int = 900) -> str: ...
