from unittest.mock import Mock
from uuid import uuid4

from app.api.admin.ingestion import SplitRequest, split_topic
from app.models import ReviewEvent, Topic, User


def test_split_topic_at_block_boundary_is_audited_and_contiguous() -> None:
    chapter_id = uuid4()
    topic = Topic(
        id=uuid4(),
        chapter_id=chapter_id,
        parent_id=None,
        position=1,
        title="Combined topic",
        content_text="First part.\n\nSecond part.",
        content_blocks=[
            {"type": "paragraph", "text": "First part."},
            {"type": "paragraph", "text": "Second part."},
        ],
        word_count=4,
        block_type="topic",
        review_status="approved",
        confidence=0.9,
        content_hash="before",
    )
    user = User(id=uuid4(), email="reviewer@example.org", password_hash="unused", role="reviewer")
    session = Mock()
    session.get.return_value = topic
    sibling_result = Mock()
    sibling_result.all.return_value = []
    session.scalars.return_value = sibling_result
    added = []
    session.add.side_effect = added.append
    session.flush.side_effect = lambda: setattr(added[0], "id", added[0].id or uuid4())

    response = split_topic(
        topic.id,
        SplitRequest(block_index=1),
        db=session,
        user=user,
    )

    assert topic.content_text == "First part."
    created = next(item for item in added if isinstance(item, Topic))
    assert created.content_text == "Second part."
    assert created.position == 2
    assert created.review_status == "pending"
    assert response["right_topic_id"] == str(created.id)
    assert any(isinstance(item, ReviewEvent) for item in added)
    session.commit.assert_called_once()
