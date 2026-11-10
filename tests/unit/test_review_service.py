from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from app.models import ReviewEvent
from app.services.review import update_topic, validate_publish


def _scalar_result(rows):
    result = Mock()
    result.__iter__ = Mock(return_value=iter(rows))
    result.all.return_value = rows
    return result


def test_publish_validator_accepts_approved_consistent_book() -> None:
    book_id, chapter_id, topic_id = uuid4(), uuid4(), uuid4()
    book = SimpleNamespace(id=book_id)
    chapter = SimpleNamespace(id=chapter_id, chapter_no=1, review_status="approved")
    topic = SimpleNamespace(
        id=topic_id,
        title="A topic",
        position=1,
        review_status="approved",
        content_text="A short exact paragraph.",
        content_blocks=[{"type": "paragraph", "text": "A short exact paragraph."}],
        block_type="topic",
        word_count=4,
    )
    db = Mock()
    db.scalars.side_effect = [
        _scalar_result([chapter]),
        _scalar_result([topic]),
        _scalar_result([]),
    ]

    assert validate_publish(db, book) == []


def test_publish_validator_reports_review_and_content_errors() -> None:
    book = SimpleNamespace(id=uuid4())
    chapter = SimpleNamespace(id=uuid4(), chapter_no=1, review_status="pending")
    topic = SimpleNamespace(
        id=uuid4(),
        title="Broken topic",
        position=2,
        review_status="needs_fix",
        content_text="incorrect text",
        content_blocks=[{"type": "figure", "asset_id": "bad-id"}],
        block_type="topic",
        word_count=3001,
    )
    db = Mock()
    db.scalars.side_effect = [
        _scalar_result([chapter]),
        _scalar_result([topic]),
        _scalar_result([]),
    ]
    db.get.return_value = None

    errors = validate_publish(db, book)
    assert any("not approved" in error for error in errors)
    assert any("contiguous" in error for error in errors)
    assert any("exceeds" in error for error in errors)
    assert any("mismatch" in error or "malformed" in error for error in errors)


def test_topic_edit_keeps_blocks_text_and_hash_consistent() -> None:
    topic = SimpleNamespace(
        id=uuid4(),
        title="Before",
        topic_code="1.1",
        content_text="Old text",
        content_blocks=[{"type": "paragraph", "text": "Old text"}],
        word_count=2,
        content_hash="old",
        review_status="approved",
    )
    user = SimpleNamespace(id=uuid4())
    db = Mock()

    updated = update_topic(db, topic, user, {"content_text": "नया पाठ"})

    assert updated.content_blocks == [{"type": "paragraph", "text": "नया पाठ"}]
    assert updated.content_text == "नया पाठ"
    assert updated.word_count == 2
    assert updated.review_status == "pending"
    db.add.assert_called_once()
    assert isinstance(db.add.call_args.args[0], ReviewEvent)
