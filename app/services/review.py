import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.flatten import flatten_blocks
from app.models import Asset, Book, Chapter, ReviewEvent, Topic, User


def record(
    db: Session,
    user: User,
    kind: str,
    entity_id: UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    db.add(
        ReviewEvent(
            actor_id=user.id,
            entity_type=kind,
            entity_id=entity_id,
            action=action,
            before_data=before,
            after_data=after,
        )
    )


def update_topic(db: Session, topic: Topic, user: User, changes: dict[str, Any]) -> Topic:
    before = {key: getattr(topic, key) for key in changes}
    for key, value in changes.items():
        if key == "content_blocks":
            topic.content_blocks = value
            topic.content_text = flatten_blocks(value)
        elif key == "content_text":
            topic.content_text = value
            topic.content_blocks = [{"type": "paragraph", "text": value}] if value else []
        elif key in {"title", "topic_code"}:
            setattr(topic, key, value)
    topic.word_count = len(topic.content_text.split())
    topic.content_hash = hashlib.sha256(topic.content_text.encode("utf-8")).hexdigest()
    topic.review_status = "pending"
    after = {key: getattr(topic, key) for key in changes}
    record(db, user, "topic", topic.id, "edit", before, after)
    db.commit()
    db.refresh(topic)
    return topic


def resequence_topics(db: Session, topics: list[Topic]) -> None:
    """Assign unique contiguous chapter positions without transient uniqueness collisions."""
    for topic in topics:
        topic.position = -topic.position
    db.flush()
    for position, topic in enumerate(topics, 1):
        topic.position = position


def validate_publish(db: Session, book: Book) -> list[str]:
    errors: list[str] = []
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.chapter_no))
    )
    if not chapters:
        errors.append("Book has no chapters")
    if len({c.chapter_no for c in chapters}) != len(chapters):
        errors.append("Chapter numbers must be unique")
    for chapter in chapters:
        if chapter.review_status != "approved":
            errors.append(f"Chapter {chapter.chapter_no} is not approved")
        topics = list(
            db.scalars(select(Topic).where(Topic.chapter_id == chapter.id).order_by(Topic.position))
        )
        for position, topic in enumerate(topics, 1):
            if topic.position != position:
                errors.append(f"Topic positions in chapter {chapter.chapter_no} must be contiguous")
            if topic.review_status != "approved":
                errors.append(f"Topic '{topic.title}' is not approved")
            if not topic.content_text.strip() and not (
                topic.block_type == "intro"
                and any(block.get("approved_placeholder") for block in topic.content_blocks)
            ):
                errors.append(f"Topic '{topic.title}' is empty")
            if topic.word_count > 3000 and not any(
                block.get("max_word_count_override") for block in topic.content_blocks
            ):
                errors.append(f"Topic '{topic.title}' exceeds the maximum word count")
            if flatten_blocks(topic.content_blocks) != topic.content_text:
                errors.append(f"Topic '{topic.title}' text does not match its content blocks")
            for block in topic.content_blocks:
                if block.get("type") == "figure":
                    try:
                        asset_id = UUID(block["asset_id"])
                    except (KeyError, ValueError):
                        errors.append(f"Topic '{topic.title}' has a malformed figure reference")
                        continue
                    if db.get(Asset, asset_id) is None:
                        errors.append(f"Topic '{topic.title}' references a missing figure")
        assets = list(db.scalars(select(Asset).where(Asset.chapter_id == chapter.id)))
        for asset in assets:
            if not asset.alt_text:
                errors.append(f"Asset {asset.id} has no alt text")
            if asset.review_status != "approved":
                errors.append(f"Asset {asset.id} is not approved")
    return errors
