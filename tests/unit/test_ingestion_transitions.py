from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.admin import ingestion
from app.models import Asset, Chapter, ReviewEvent, Topic


def _result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def test_job_read_report_cancel_and_retry_status_paths(monkeypatch) -> None:
    job_id = uuid4()
    stage = SimpleNamespace(
        stage="text_extraction",
        status="succeeded",
        started_at=None,
        finished_at=None,
        duration_ms=12,
    )
    job = SimpleNamespace(
        id=job_id,
        book_id=None,
        status="queued",
        current_stage=None,
        progress_percent=0,
        error_message=None,
        stats={"quality_report": {"chapter_count": 1}},
        finished_at=None,
    )
    db = Mock()
    db.get.return_value = job
    db.scalars.return_value = _result([stage])
    user = SimpleNamespace(id=uuid4())

    assert ingestion.get_job(job_id, db, user)["stages"][0]["stage"] == "text_extraction"
    assert ingestion.job_report(job_id, db, user) == {"chapter_count": 1}
    assert ingestion.cancel_job(job_id, db, user) == {"status": "cancelled"}
    db.commit.assert_called_once()
    job.status = "succeeded"
    assert ingestion.cancel_job(job_id, db, user) == {"status": "succeeded"}
    with pytest.raises(HTTPException) as error:
        ingestion.retry_job(job_id, db, user)
    assert error.value.status_code == 409


def test_chapter_and_topic_approvals_are_audited() -> None:
    chapter_id, topic_id, user_id = uuid4(), uuid4(), uuid4()
    chapter = Chapter(id=chapter_id, book_id=uuid4(), chapter_no=1, title="Chapter")
    topic = Topic(
        id=topic_id,
        chapter_id=chapter.id,
        position=1,
        title="Topic",
        content_text="Text",
        content_blocks=[{"type": "paragraph", "text": "Text"}],
        block_type="topic",
        review_status="pending",
        confidence=0.9,
    )
    user = SimpleNamespace(id=user_id)
    db = Mock()
    db.get.side_effect = [chapter, topic]
    events = []
    db.add.side_effect = events.append

    assert ingestion.approve_chapter(chapter_id, db, user)["review_status"] == "approved"
    assert ingestion.approve_topic(topic_id, db, user)["review_status"] == "approved"
    assert len(events) == 2
    assert all(isinstance(event, ReviewEvent) for event in events)


def test_tree_summary_and_delete_resequence_paths() -> None:
    book_id, chapter_id, topic_id = uuid4(), uuid4(), uuid4()
    book = SimpleNamespace(id=book_id, title="Book", status="in_review")
    chapter = SimpleNamespace(
        id=chapter_id, book_id=book_id, chapter_no=1, title="Chapter", review_status="pending"
    )
    topic = SimpleNamespace(
        id=topic_id,
        chapter_id=chapter_id,
        parent_id=None,
        position=1,
        topic_code="1.1",
        title="Topic",
        content_text="Short text",
        review_status="pending",
        word_count=10,
    )
    db = Mock()
    db.get.return_value = book
    db.scalars.side_effect = [
        _result([chapter]),
        _result([topic]),
        _result([chapter]),
        _result([topic]),
        _result([]),
    ]
    tree = ingestion.book_tree(book_id, True, db, SimpleNamespace(id=uuid4()))
    assert tree["chapters"][0]["topics"][0]["flags"] == ["short"]
    summary = ingestion.review_summary(book_id, db, SimpleNamespace(id=uuid4()))
    assert summary["topics"] == {"total": 1, "pending": 1, "flagged": 1}

    db.get.return_value = topic
    ingestion.delete_topic(topic_id, db, SimpleNamespace(id=uuid4()))
    db.delete.assert_called_once_with(topic)
    db.commit.assert_called_once()


def test_asset_caption_edit_is_audited_and_sets_pending() -> None:
    asset_id = uuid4()
    asset = Asset(
        id=asset_id,
        book_id=uuid4(),
        chapter_id=uuid4(),
        topic_id=None,
        kind="image",
        file_id=uuid4(),
        position=0,
        review_status="approved",
    )
    db = Mock()
    db.get.return_value = asset
    user = SimpleNamespace(id=uuid4())
    event_log = []
    db.add.side_effect = event_log.append

    updated = ingestion.patch_asset(
        asset_id,
        ingestion.AssetPatch(caption="New caption"),
        db,
        user,
    )

    assert updated["caption"] == "New caption"
    assert asset.review_status == "pending"
    assert isinstance(event_log[0], ReviewEvent)


def test_missing_job_returns_not_found() -> None:
    db = Mock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as error:
        ingestion.job_report(uuid4(), db, SimpleNamespace(id=uuid4()))

    assert error.value.status_code == 404
