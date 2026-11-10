from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.admin import ingestion, publish
from app.models import ReviewEvent


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]


def test_publish_updates_version_audit_and_public_epoch(monkeypatch) -> None:
    book_id, user_id = uuid4(), uuid4()
    book = SimpleNamespace(id=book_id, status="in_review", published_version=2)
    user = SimpleNamespace(id=user_id)
    db = Mock()
    db.get.return_value = book
    db.add.side_effect = lambda event: setattr(event, "id", uuid4())
    redis = FakeRedis()
    monkeypatch.setattr(publish, "validate_publish", lambda *_: [])
    monkeypatch.setattr(publish, "get_redis", lambda: redis)

    result = publish.publish(book_id, db, user)

    assert result == {"id": str(book_id), "status": "published", "published_version": 3}
    assert book.published_at is not None
    assert book.published_by == user_id
    assert redis.values == {"public:epoch": 1}
    assert isinstance(db.add.call_args.args[0], ReviewEvent)
    db.commit.assert_called_once()


def test_publish_blocks_invalid_book_without_mutating(monkeypatch) -> None:
    book_id = uuid4()
    book = SimpleNamespace(id=book_id, status="in_review", published_version=0)
    db = Mock()
    db.get.return_value = book
    monkeypatch.setattr(publish, "validate_publish", lambda *_: ["Topic is not approved"])

    with pytest.raises(HTTPException) as error:
        publish.publish(book_id, db, SimpleNamespace(id=uuid4()))

    assert error.value.status_code == 409
    assert book.status == "in_review"
    db.commit.assert_not_called()


def test_unpublish_archives_book_and_invalidates_global_cache(monkeypatch) -> None:
    book_id = uuid4()
    book = SimpleNamespace(id=book_id, status="published", published_version=4)
    user = SimpleNamespace(id=uuid4())
    db = Mock()
    db.get.return_value = book
    redis = FakeRedis()
    monkeypatch.setattr(publish, "get_redis", lambda: redis)

    result = publish.unpublish(book_id, db, user)

    assert result == {"id": str(book_id), "status": "archived"}
    assert book.published_version == 5
    assert redis.values == {"public:epoch": 1}
    db.commit.assert_called_once()


def test_retry_requeues_failed_job(monkeypatch) -> None:
    job_id = uuid4()
    job = SimpleNamespace(
        id=job_id,
        status="failed",
        error_message="parse failed",
        progress_percent=70,
        finished_at=object(),
    )
    db = Mock()
    db.get.return_value = job
    queued: list[str] = []
    monkeypatch.setattr(ingestion.process_book, "delay", queued.append)

    result = ingestion.retry_job(job_id, db, SimpleNamespace(id=uuid4()))

    assert result == {"job_id": str(job_id), "status": "queued"}
    assert queued == [str(job_id)]
    assert job.error_message is None
    assert job.progress_percent == 0
    assert job.finished_at is None
    db.commit.assert_called_once()


def test_retry_rejects_running_job() -> None:
    job = SimpleNamespace(id=uuid4(), status="running")
    db = Mock()
    db.get.return_value = job

    with pytest.raises(HTTPException) as error:
        ingestion.retry_job(job.id, db, SimpleNamespace(id=uuid4()))

    assert error.value.status_code == 409
    db.commit.assert_not_called()
