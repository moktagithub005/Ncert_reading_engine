import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_roles
from app.cache.redis import get_redis
from app.ingestion.flatten import flatten_blocks
from app.models import Asset, Book, Chapter, FileRecord, Topic, User
from app.services.review import record, validate_publish

router = APIRouter(tags=["admin-publish"])


class ImportTopic(BaseModel):
    id: UUID | None = None
    parent_id: UUID | None = None
    position: int = Field(ge=1)
    topic_code: str | None = None
    title: str = Field(min_length=1, max_length=500)
    content_text: str = ""
    content_blocks: list[dict[str, Any]] = Field(default_factory=list)
    word_count: int = Field(default=0, ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    block_type: str = "topic"
    confidence: float = Field(default=1, ge=0, le=1)
    content_hash: str = ""


class ImportChapter(BaseModel):
    id: UUID | None = None
    chapter_no: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    start_page: int | None = Field(default=None, ge=1)
    end_page: int | None = Field(default=None, ge=1)
    topics: list[ImportTopic] = Field(default_factory=list)


class ImportBook(BaseModel):
    class_no: int = Field(ge=1, le=12)
    subject: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    language: str = Field(pattern="^(en|hi)$")
    edition: str = Field(min_length=1, max_length=64)


class ImportAsset(BaseModel):
    id: UUID | None = None
    chapter_id: UUID | None = None
    topic_id: UUID | None = None
    kind: str
    file_id: UUID
    caption: str | None = None
    alt_text: str | None = None
    figure_label: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: dict[str, Any] | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    position: int = Field(default=0, ge=0)


class BookImportRequest(BaseModel):
    book: ImportBook
    chapters: list[ImportChapter]
    assets: list[ImportAsset] = Field(default_factory=list)


@router.post("/books/{book_id}/publish")
def publish(
    book_id: UUID, db: Session = Depends(db_session), user: User = Depends(require_roles("admin"))
) -> dict[str, Any]:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    errors = validate_publish(db, book)
    if errors:
        raise HTTPException(409, {"message": "Book failed publish validation", "errors": errors})
    before = {"status": book.status, "published_version": book.published_version}
    book.status, book.published_at, book.published_by = (
        "published",
        datetime.now(UTC),
        user.id,
    )
    book.published_version += 1
    record(
        db,
        user,
        "book",
        book.id,
        "publish",
        before,
        {"status": book.status, "published_version": book.published_version},
    )
    db.commit()
    try:
        get_redis().incr("public:epoch")
    except RedisError:
        pass
    return {"id": str(book.id), "status": book.status, "published_version": book.published_version}


@router.post("/books/{book_id}/unpublish")
def unpublish(
    book_id: UUID, db: Session = Depends(db_session), user: User = Depends(require_roles("admin"))
) -> dict[str, Any]:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    before = {"status": book.status}
    book.status = "archived"
    book.published_version += 1
    record(db, user, "book", book.id, "publish", before, {"status": book.status})
    db.commit()
    try:
        get_redis().incr("public:epoch")
    except RedisError:
        pass
    return {"id": str(book.id), "status": book.status}


@router.get("/books/{book_id}/export")
def export_book(
    book_id: UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer", "viewer")),
) -> dict[str, Any]:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.chapter_no)
    ).all()
    output: dict[str, Any] = {
        "book": {
            "id": str(book.id),
            "class_no": book.class_no,
            "subject": book.subject,
            "title": book.title,
            "language": book.language,
            "edition": book.edition,
        },
        "chapters": [],
    }
    for chapter in chapters:
        topics = db.scalars(
            select(Topic).where(Topic.chapter_id == chapter.id).order_by(Topic.position)
        ).all()
        output["chapters"].append(
            {
                "id": str(chapter.id),
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "start_page": chapter.start_page,
                "end_page": chapter.end_page,
                "topics": [
                    {
                        "id": str(t.id),
                        "position": t.position,
                        "parent_id": str(t.parent_id) if t.parent_id else None,
                        "topic_code": t.topic_code,
                        "title": t.title,
                        "content_text": t.content_text,
                        "content_blocks": t.content_blocks,
                        "word_count": t.word_count,
                        "page_start": t.page_start,
                        "page_end": t.page_end,
                        "block_type": t.block_type,
                        "review_status": t.review_status,
                        "confidence": t.confidence,
                        "content_hash": t.content_hash,
                    }
                    for t in topics
                ],
            }
        )
    output["assets"] = [
        {
            "id": str(a.id),
            "chapter_id": str(a.chapter_id) if a.chapter_id else None,
            "topic_id": str(a.topic_id) if a.topic_id else None,
            "kind": a.kind,
            "file_id": str(a.file_id),
            "caption": a.caption,
            "alt_text": a.alt_text,
            "figure_label": a.figure_label,
            "page": a.page,
            "bbox": a.bbox,
            "width": a.width,
            "height": a.height,
            "position": a.position,
        }
        for a in db.scalars(select(Asset).where(Asset.book_id == book.id))
    ]
    return output


@router.post("/books/import", status_code=201)
def import_book(
    body: BookImportRequest,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    meta = body.book
    book = Book(
        class_no=meta.class_no,
        subject=meta.subject,
        title=meta.title,
        language=meta.language,
        edition=meta.edition,
        status="in_review",
    )
    db.add(book)
    db.flush()
    chapter_ids: dict[UUID, UUID] = {}
    topic_ids: dict[UUID, UUID] = {}
    imported_topics: dict[UUID, Topic] = {}
    for ch in body.chapters:
        chapter = Chapter(
            book_id=book.id,
            chapter_no=ch.chapter_no,
            title=ch.title,
            start_page=ch.start_page,
            end_page=ch.end_page,
            review_status="pending",
            confidence=1.0,
        )
        db.add(chapter)
        db.flush()
        if ch.id:
            chapter_ids[ch.id] = chapter.id
        for topic_data in ch.topics:
            blocks = topic_data.content_blocks
            content_text = topic_data.content_text
            if blocks and flatten_blocks(blocks) != content_text:
                raise HTTPException(422, f"Content blocks do not match topic '{topic_data.title}'")
            if not blocks and content_text:
                blocks = [{"type": "paragraph", "text": content_text}]
            topic = Topic(
                chapter_id=chapter.id,
                parent_id=topic_ids.get(topic_data.parent_id) if topic_data.parent_id else None,
                position=topic_data.position,
                topic_code=topic_data.topic_code,
                title=topic_data.title,
                content_text=content_text,
                content_blocks=blocks,
                word_count=topic_data.word_count or len(content_text.split()),
                page_start=topic_data.page_start,
                page_end=topic_data.page_end,
                block_type=topic_data.block_type,
                review_status="pending",
                confidence=topic_data.confidence,
                content_hash=hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
            )
            db.add(topic)
            db.flush()
            if topic_data.id:
                topic_ids[topic_data.id] = topic.id
                imported_topics[topic_data.id] = topic
    asset_ids: dict[UUID, UUID] = {}
    for asset_data in body.assets:
        if db.get(FileRecord, asset_data.file_id) is None:
            raise HTTPException(422, f"Asset file {asset_data.file_id} is unavailable")
        asset = Asset(
            book_id=book.id,
            chapter_id=chapter_ids.get(asset_data.chapter_id) if asset_data.chapter_id else None,
            topic_id=topic_ids.get(asset_data.topic_id) if asset_data.topic_id else None,
            kind=asset_data.kind,
            file_id=asset_data.file_id,
            caption=asset_data.caption,
            alt_text=asset_data.alt_text,
            figure_label=asset_data.figure_label,
            page=asset_data.page,
            bbox=asset_data.bbox,
            width=asset_data.width,
            height=asset_data.height,
            position=asset_data.position,
            review_status="pending",
            confidence=1.0,
        )
        db.add(asset)
        db.flush()
        if asset_data.id:
            asset_ids[asset_data.id] = asset.id
    for topic in imported_topics.values():
        updated_blocks = []
        for block in topic.content_blocks:
            if block.get("type") == "figure":
                try:
                    old_asset_id = UUID(block["asset_id"])
                    block = {**block, "asset_id": str(asset_ids[old_asset_id])}
                except (KeyError, ValueError) as exc:
                    raise HTTPException(
                        422, "Imported figure references an unavailable asset"
                    ) from exc
            updated_blocks.append(block)
        topic.content_blocks = updated_blocks
        topic.content_text = flatten_blocks(updated_blocks)
        topic.word_count = len(topic.content_text.split())
        topic.content_hash = hashlib.sha256(topic.content_text.encode("utf-8")).hexdigest()
    record(db, user, "book", book.id, "edit", None, {"imported": True})
    db.commit()
    return {"id": str(book.id), "status": book.status}
