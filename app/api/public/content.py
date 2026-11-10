import hashlib
import json
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache.redis import get_redis
from app.core.metrics import CACHE_HITS
from app.db.session import get_db
from app.models import Asset, Book, Chapter, FileRecord, Topic
from app.storage import get_storage

router = APIRouter(tags=["public-content"])
HEADERS = {"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"}


def _response(request: Request, payload: dict[str, Any], etag: str) -> Response:
    etag = f'"{etag}"'
    headers = {**HEADERS, "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        media_type="application/json",
        headers=headers,
    )


def _cached(key: str) -> dict[str, Any] | None:
    redis = get_redis()
    try:
        raw = cast(str | None, redis.get(key))
    except RedisError:
        CACHE_HITS.labels("error").inc()
        return None
    if raw:
        CACHE_HITS.labels("hit").inc()
        cached = json.loads(raw)
        return cast(dict[str, Any], cached)
    CACHE_HITS.labels("miss").inc()
    return None


def _store(key: str, value: dict[str, Any]) -> None:
    try:
        get_redis().setex(key, 3600, json.dumps(value, ensure_ascii=False, default=str))
    except RedisError:
        CACHE_HITS.labels("error").inc()


def _global_cache_key(name: str) -> str:
    try:
        epoch = get_redis().get("public:epoch") or "0"
    except RedisError:
        epoch = "0"
    return f"public:epoch:{epoch}:{name}"


@router.get("/classes")
def classes(request: Request, db: Session = Depends(get_db)) -> Response:
    key = _global_cache_key("classes")
    payload = _cached(key)
    if payload is None:
        values = db.scalars(
            select(Book.class_no)
            .where(Book.status == "published")
            .distinct()
            .order_by(Book.class_no)
        ).all()
        payload = {"classes": values}
        _store(key, payload)
    etag = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return _response(request, payload, etag)


@router.get("/classes/{class_no}/subjects")
def subjects(class_no: int, request: Request, db: Session = Depends(get_db)) -> Response:
    key = _global_cache_key(f"subjects:{class_no}")
    payload = _cached(key)
    if payload is None:
        books = db.scalars(
            select(Book)
            .where(Book.class_no == class_no, Book.status == "published")
            .order_by(Book.subject, Book.language)
        ).all()
        payload = {
            "subjects": [
                {
                    "subject": book.subject,
                    "language": book.language,
                    "book_id": str(book.id),
                    "title": book.title,
                    "edition": book.edition,
                }
                for book in books
            ]
        }
        _store(key, payload)
    etag = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return _response(request, payload, etag)


@router.get("/books/{book_id}")
def book_detail(book_id: UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    book = db.get(Book, book_id)
    if not book or book.status != "published":
        raise HTTPException(404, "Book not found")
    payload = {
        "id": str(book.id),
        "class_no": book.class_no,
        "subject": book.subject,
        "title": book.title,
        "language": book.language,
        "edition": book.edition,
        "published_version": book.published_version,
    }
    return _response(
        request, payload, hashlib.sha256(f"{book.id}:{book.published_version}".encode()).hexdigest()
    )


@router.get("/books/{book_id}/chapters")
def chapters(
    book_id: UUID,
    request: Request,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> Response:
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(422, "Invalid pagination values")
    book = db.get(Book, book_id)
    if not book or book.status != "published":
        raise HTTPException(404, "Book not found")
    key = f"public:{book.id}:{book.published_version}:chapters:{limit}:{offset}"
    payload = _cached(key)
    if payload is None:
        values = db.scalars(
            select(Chapter)
            .where(Chapter.book_id == book.id)
            .order_by(Chapter.chapter_no)
            .limit(limit)
            .offset(offset)
        ).all()
        payload = {
            "chapters": [
                {
                    "id": str(c.id),
                    "chapter_no": c.chapter_no,
                    "title": c.title,
                    "start_page": c.start_page,
                    "end_page": c.end_page,
                }
                for c in values
            ]
        }
        _store(key, payload)
    return _response(
        request, payload, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    )


@router.get("/chapters/{chapter_id}/topics")
def topics(
    chapter_id: UUID,
    request: Request,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> Response:
    if not 1 <= limit <= 1000 or offset < 0:
        raise HTTPException(422, "Invalid pagination values")
    chapter = db.get(Chapter, chapter_id)
    book = db.get(Book, chapter.book_id) if chapter else None
    if not chapter or not book or book.status != "published":
        raise HTTPException(404, "Chapter not found")
    key = f"public:{book.id}:{book.published_version}:topics:{chapter.id}:{limit}:{offset}"
    payload = _cached(key)
    if payload is None:
        values = db.scalars(
            select(Topic)
            .where(Topic.chapter_id == chapter.id)
            .order_by(Topic.position)
            .limit(limit)
            .offset(offset)
        ).all()
        payload = {
            "topics": [
                {
                    "id": str(t.id),
                    "code": t.topic_code,
                    "title": t.title,
                    "parent_id": str(t.parent_id) if t.parent_id else None,
                    "position": t.position,
                    "word_count": t.word_count,
                }
                for t in values
            ]
        }
        _store(key, payload)
    return _response(
        request, payload, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    )


@router.get("/topics/{topic_id}")
def topic_detail(topic_id: UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    topic = db.get(Topic, topic_id)
    chapter = db.get(Chapter, topic.chapter_id) if topic else None
    book = db.get(Book, chapter.book_id) if chapter else None
    if not topic or not chapter or not book or book.status != "published":
        raise HTTPException(404, "Topic not found")
    key = f"public:{book.id}:{book.published_version}:topic:{topic.id}"
    payload = _cached(key)
    if payload is None:
        assets = db.scalars(
            select(Asset).where(Asset.topic_id == topic.id).order_by(Asset.position)
        ).all()
        all_topics = db.scalars(
            select(Topic).where(Topic.chapter_id == chapter.id).order_by(Topic.position)
        ).all()
        index = next((i for i, item in enumerate(all_topics) if item.id == topic.id), 0)
        payload = {
            "id": str(topic.id),
            "code": topic.topic_code,
            "title": topic.title,
            "content_text": topic.content_text,
            "content_blocks": topic.content_blocks,
            "word_count": topic.word_count,
            "assets": [
                {
                    "id": str(a.id),
                    "kind": a.kind,
                    "caption": a.caption,
                    "alt_text": a.alt_text,
                    "page": a.page,
                }
                for a in assets
            ],
            "breadcrumb": {
                "book": {"id": str(book.id), "title": book.title},
                "chapter": {"id": str(chapter.id), "title": chapter.title},
            },
            "previous_topic_id": str(all_topics[index - 1].id) if index > 0 else None,
            "next_topic_id": str(all_topics[index + 1].id) if index + 1 < len(all_topics) else None,
        }
        _store(key, payload)
    content_hash = topic.content_hash or hashlib.sha256(topic.content_text.encode()).hexdigest()
    etag = hashlib.sha256(f"{content_hash}:{book.published_version}".encode()).hexdigest()
    return _response(request, payload, etag)


@router.get("/assets/{asset_id}")
def asset(asset_id: UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    value = db.get(Asset, asset_id)
    book = db.get(Book, value.book_id) if value else None
    file_record = db.get(FileRecord, value.file_id) if value else None
    if not value or not book or book.status != "published" or not file_record:
        raise HTTPException(404, "Asset not found")
    if file_record.kind != "image":
        raise HTTPException(404, "Asset file is not publicly servable")
    etag = f'"{file_record.sha256}"'
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": etag,
    }
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return StreamingResponse(
        get_storage().open(file_record.storage_key),
        media_type="image/webp",
        headers=headers,
    )
