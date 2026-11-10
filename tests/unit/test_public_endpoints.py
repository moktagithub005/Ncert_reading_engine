from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.public import content
from app.db.session import get_db
from app.main import app
from app.models import Asset, Book, Chapter, FileRecord, Topic


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, _ttl, value):
        self.values[key] = value


class FakeStorage:
    def open(self, _key):
        return iter([b"webp-data"])


@pytest.fixture
def published_catalog(monkeypatch):
    book_id, chapter_id = uuid4(), uuid4()
    topic_ids = [uuid4(), uuid4()]
    asset_id, file_id = uuid4(), uuid4()
    book = SimpleNamespace(
        id=book_id,
        class_no=6,
        subject="Science",
        title="Fixture Book",
        language="en",
        edition="2026",
        status="published",
        published_version=3,
    )
    chapter = SimpleNamespace(
        id=chapter_id,
        book_id=book_id,
        chapter_no=1,
        title="Materials",
        start_page=1,
        end_page=5,
    )
    first = SimpleNamespace(
        id=topic_ids[0],
        chapter_id=chapter_id,
        topic_code="1.1",
        title="First topic",
        parent_id=None,
        position=1,
        word_count=24,
        content_text="The first exact paragraph.",
        content_blocks=[{"type": "paragraph", "text": "The first exact paragraph."}],
        content_hash="first-hash",
    )
    second = SimpleNamespace(
        id=topic_ids[1],
        chapter_id=chapter_id,
        topic_code="1.2",
        title="Second topic",
        parent_id=None,
        position=2,
        word_count=31,
        content_text="The second exact paragraph.",
        content_blocks=[{"type": "paragraph", "text": "The second exact paragraph."}],
        content_hash="second-hash",
    )
    asset = SimpleNamespace(
        id=asset_id,
        book_id=book_id,
        topic_id=topic_ids[1],
        file_id=file_id,
        position=1,
        kind="image",
        caption="A sample image",
        alt_text="sample image",
        page=2,
    )
    file_record = SimpleNamespace(
        id=file_id,
        kind="image",
        sha256="a" * 64,
        storage_key="assets/a/sample.webp",
    )
    objects = {
        (Book, book_id): book,
        (Chapter, chapter_id): chapter,
        (Topic, topic_ids[0]): first,
        (Topic, topic_ids[1]): second,
        (Asset, asset_id): asset,
        (FileRecord, file_id): file_record,
    }
    db = Mock()
    db.get.side_effect = lambda model, key: objects.get((model, key))
    db.scalars.side_effect = [
        Result([6]),
        Result([book]),
        Result([chapter]),
        Result([first, second]),
        Result([asset]),
        Result([first, second]),
    ]
    redis = FakeRedis()
    monkeypatch.setattr(content, "get_redis", lambda: redis)
    monkeypatch.setattr(content, "get_storage", lambda: FakeStorage())
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            yield client, book_id, chapter_id, topic_ids, asset_id, db
    finally:
        app.dependency_overrides.clear()


def test_published_catalog_endpoints_and_immutable_asset(published_catalog) -> None:
    client, book_id, chapter_id, topic_ids, asset_id, db = published_catalog

    classes = client.get("/api/v1/classes")
    assert classes.json() == {"classes": [6]}
    subjects = client.get("/api/v1/classes/6/subjects")
    assert subjects.json()["subjects"][0]["book_id"] == str(book_id)
    book = client.get(f"/api/v1/books/{book_id}")
    assert book.json()["published_version"] == 3
    chapters = client.get(f"/api/v1/books/{book_id}/chapters?limit=10")
    assert chapters.json()["chapters"][0]["id"] == str(chapter_id)
    topic_list = client.get(f"/api/v1/chapters/{chapter_id}/topics?limit=10")
    assert len(topic_list.json()["topics"]) == 2
    detail = client.get(f"/api/v1/topics/{topic_ids[1]}")
    assert detail.json()["content_text"] == "The second exact paragraph."
    assert detail.json()["previous_topic_id"] == str(topic_ids[0])
    assert detail.json()["assets"][0]["alt_text"] == "sample image"
    assert (
        client.get(
            f"/api/v1/topics/{topic_ids[1]}", headers={"If-None-Match": detail.headers["etag"]}
        ).status_code
        == 304
    )
    streamed = client.get(f"/api/v1/assets/{asset_id}")
    assert streamed.content == b"webp-data"
    assert streamed.headers["cache-control"].endswith("immutable")
    assert db.scalars.call_count == 6


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/v1/books/00000000-0000-0000-0000-000000000001/chapters?limit=0", 422),
        ("/api/v1/chapters/00000000-0000-0000-0000-000000000002/topics?offset=-1", 422),
        ("/api/v1/books/00000000-0000-0000-0000-000000000099", 404),
        ("/api/v1/assets/00000000-0000-0000-0000-000000000099", 404),
    ],
)
def test_public_api_rejects_invalid_pagination_or_unpublished_records(
    path, status, monkeypatch
) -> None:
    db = Mock()
    db.get.return_value = None
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(content, "get_redis", lambda: FakeRedis())
    try:
        with TestClient(app) as client:
            assert client.get(path).status_code == status
    finally:
        app.dependency_overrides.clear()
