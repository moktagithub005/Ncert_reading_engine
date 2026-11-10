from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_roles
from app.models import Book, Chapter, IngestionJob, IngestionStageRun, User
from app.workers.tasks import process_book

router = APIRouter(tags=["admin-ingestion"])


class SplitRequest(BaseModel):
    character_offset: int | None = Field(default=None, ge=0)
    block_index: int | None = Field(default=None, ge=0)


class ChapterPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    chapter_no: int | None = Field(default=None, ge=1)


class TopicPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    topic_code: str | None = Field(default=None, max_length=64)
    content_text: str | None = None
    content_blocks: list[dict[str, Any]] | None = None


class MergeRequest(BaseModel):
    topic_ids: list[UUID] = Field(min_length=2)


class MoveRequest(BaseModel):
    parent_id: UUID | None = None
    position: int = Field(ge=1)


class AssetPatch(BaseModel):
    caption: str | None = None
    alt_text: str | None = None
    topic_id: UUID | None = None
    position: int | None = Field(default=None, ge=0)
    review_status: str | None = None


@router.get("/jobs/{job_id}")
def get_job(
    job_id: UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer", "viewer")),
) -> dict[str, Any]:
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    stages = db.scalars(
        select(IngestionStageRun)
        .where(IngestionStageRun.job_id == job.id)
        .order_by(IngestionStageRun.started_at)
    ).all()
    return {
        "id": str(job.id),
        "book_id": str(job.book_id) if job.book_id else None,
        "status": job.status,
        "current_stage": job.current_stage,
        "progress_percent": job.progress_percent,
        "error_message": job.error_message,
        "stages": [
            {
                "stage": run.stage,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_ms": run.duration_ms,
            }
            for run in stages
        ],
    }


@router.get("/jobs/{job_id}/report")
def job_report(
    job_id: UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer", "viewer")),
) -> dict[str, Any]:
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return cast(dict[str, Any], job.stats.get("quality_report", {}))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, str]:
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status in {"queued", "running"}:
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
        db.commit()
    return {"status": job.status}


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, str]:
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    job.status = "queued"
    job.error_message = None
    job.progress_percent = 0
    job.finished_at = None
    db.commit()
    process_book.delay(str(job.id))
    return {"job_id": str(job.id), "status": "queued"}


@router.get("/books/{book_id}/tree")
def book_tree(
    book_id: UUID,
    include_flags: bool = False,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer", "viewer")),
) -> dict[str, Any]:
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "Book not found")
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)
    ).all()
    result = []
    from app.models import Topic

    for chapter in chapters:
        topics = db.scalars(
            select(Topic).where(Topic.chapter_id == chapter.id).order_by(Topic.position)
        ).all()
        result.append(
            {
                "id": str(chapter.id),
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "review_status": chapter.review_status,
                "topics": [
                    {
                        "id": str(t.id),
                        "parent_id": str(t.parent_id) if t.parent_id else None,
                        "position": t.position,
                        "code": t.topic_code,
                        "title": t.title,
                        "review_status": t.review_status,
                        **(
                            {
                                "flags": (["short"] if t.word_count < 20 else [])
                                + (["long"] if t.word_count > 3000 else [])
                            }
                            if include_flags
                            else {}
                        ),
                    }
                    for t in topics
                ],
            }
        )
    return {"id": str(book.id), "title": book.title, "status": book.status, "chapters": result}


@router.patch("/chapters/{chapter_id}")
def edit_chapter(
    chapter_id: UUID,
    body: ChapterPatch,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No editable fields supplied")
    before = {key: getattr(chapter, key) for key in changes}
    for key, value in changes.items():
        setattr(chapter, key, value)
    from app.services.review import record

    record(
        db,
        user,
        "chapter",
        chapter.id,
        "edit",
        before,
        {key: getattr(chapter, key) for key in changes},
    )
    chapter.review_status = "pending"
    db.commit()
    return {"id": str(chapter.id), "title": chapter.title, "chapter_no": chapter.chapter_no}


@router.patch("/topics/{topic_id}")
def edit_topic(
    topic_id: UUID,
    body: TopicPatch,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    from app.models import Topic
    from app.services.review import update_topic

    topic = db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    allowed = body.model_dump(exclude_unset=True)
    if not allowed:
        raise HTTPException(422, "No editable fields supplied")
    topic = update_topic(db, topic, user, allowed)
    return {"id": str(topic.id), "title": topic.title, "content_text": topic.content_text}


@router.post("/chapters/{chapter_id}/approve")
def approve_chapter(
    chapter_id: UUID,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    from app.services.review import record

    record(
        db,
        user,
        "chapter",
        chapter.id,
        "approve",
        {"review_status": chapter.review_status},
        {"review_status": "approved"},
    )
    chapter.review_status = "approved"
    db.commit()
    return {"id": str(chapter.id), "review_status": chapter.review_status}


@router.post("/topics/{topic_id}/approve")
def approve_topic(
    topic_id: UUID,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    from app.models import Topic
    from app.services.review import record

    topic = db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    record(
        db,
        user,
        "topic",
        topic.id,
        "approve",
        {"review_status": topic.review_status},
        {"review_status": "approved"},
    )
    topic.review_status = "approved"
    db.commit()
    return {"id": str(topic.id), "review_status": topic.review_status}


@router.get("/books/{book_id}/review-summary")
def review_summary(
    book_id: UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_roles("admin", "reviewer", "viewer")),
) -> dict[str, Any]:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    from app.models import Topic

    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id)).all()
    topics = db.scalars(select(Topic).join(Chapter).where(Chapter.book_id == book_id)).all()
    return {
        "chapters": {
            "total": len(chapters),
            "pending": sum(c.review_status != "approved" for c in chapters),
        },
        "topics": {
            "total": len(topics),
            "pending": sum(t.review_status != "approved" for t in topics),
            "flagged": sum(t.word_count < 20 or t.word_count > 3000 for t in topics),
        },
    }


@router.post("/topics/{topic_id}/split", status_code=201)
def split_topic(
    topic_id: UUID,
    body: SplitRequest,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    from app.ingestion.flatten import flatten_blocks
    from app.models import Topic

    topic = db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    before = {
        "title": topic.title,
        "content_blocks": topic.content_blocks,
        "position": topic.position,
    }
    if body.block_index is not None:
        if body.block_index <= 0 or body.block_index >= len(topic.content_blocks):
            raise HTTPException(422, "Block index must divide the topic")
        left_blocks, right_blocks = (
            topic.content_blocks[: body.block_index],
            topic.content_blocks[body.block_index :],
        )
    elif body.character_offset is not None:
        if body.character_offset <= 0 or body.character_offset >= len(topic.content_text):
            raise HTTPException(422, "Character offset must divide the topic")
        left_blocks = [
            {"type": "paragraph", "text": topic.content_text[: body.character_offset].strip()}
        ]
        right_blocks = [
            {"type": "paragraph", "text": topic.content_text[body.character_offset :].strip()}
        ]
    else:
        raise HTTPException(422, "Provide character_offset or block_index")
    topic.content_blocks = left_blocks
    topic.content_text = flatten_blocks(left_blocks)
    topic.word_count = len(topic.content_text.split())
    topic.review_status = "pending"
    import hashlib

    topic.content_hash = hashlib.sha256(topic.content_text.encode()).hexdigest()
    siblings = db.scalars(
        select(Topic)
        .where(Topic.chapter_id == topic.chapter_id, Topic.position > topic.position)
        .order_by(Topic.position.desc())
    ).all()
    for sibling in siblings:
        sibling.position += 1
    new_topic = Topic(
        chapter_id=topic.chapter_id,
        parent_id=topic.parent_id,
        position=topic.position + 1,
        topic_code=None,
        title=f"{topic.title} (continued)",
        content_blocks=right_blocks,
        content_text=flatten_blocks(right_blocks),
        word_count=len(flatten_blocks(right_blocks).split()),
        page_start=topic.page_start,
        page_end=topic.page_end,
        block_type=topic.block_type,
        review_status="pending",
        confidence=topic.confidence,
    )
    new_topic.content_hash = hashlib.sha256(new_topic.content_text.encode()).hexdigest()
    db.add(new_topic)
    db.flush()
    from app.services.review import record

    record(
        db,
        user,
        "topic",
        topic.id,
        "split",
        before,
        {"created_topic_id": str(new_topic.id), "position": topic.position},
    )
    db.commit()
    return {"left_topic_id": str(topic.id), "right_topic_id": str(new_topic.id)}


@router.post("/topics/merge")
def merge_topics(
    body: MergeRequest,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    from app.ingestion.flatten import flatten_blocks
    from app.models import Topic
    from app.services.review import record

    selected = list(
        db.scalars(select(Topic).where(Topic.id.in_(body.topic_ids)).order_by(Topic.position)).all()
    )
    if len(selected) != len(set(body.topic_ids)) or len(selected) < 2:
        raise HTTPException(404, "Topics not found")
    if len({(t.chapter_id, t.parent_id) for t in selected}) != 1 or any(
        b.position != a.position + 1 for a, b in zip(selected, selected[1:], strict=False)
    ):
        raise HTTPException(422, "Topics must be adjacent siblings in one chapter")
    target = selected[0]
    before = {
        "topic_ids": [str(t.id) for t in selected],
        "blocks": [t.content_blocks for t in selected],
    }
    target.content_blocks = [block for topic in selected for block in topic.content_blocks]
    target.content_text = flatten_blocks(target.content_blocks)
    target.word_count = len(target.content_text.split())
    target.review_status = "pending"
    import hashlib

    target.content_hash = hashlib.sha256(target.content_text.encode()).hexdigest()
    from app.models import Asset

    for topic in selected[1:]:
        for asset in db.scalars(select(Asset).where(Asset.topic_id == topic.id)):
            asset.topic_id = target.id
        db.delete(topic)
    record(
        db, user, "topic", target.id, "merge", before, {"merged_ids": [str(t.id) for t in selected]}
    )
    db.flush()
    survivors = list(
        db.scalars(
            select(Topic).where(Topic.chapter_id == target.chapter_id).order_by(Topic.position)
        ).all()
    )
    from app.services.review import resequence_topics

    resequence_topics(db, survivors)
    db.commit()
    return {"id": str(target.id), "content_text": target.content_text}


@router.post("/topics/{topic_id}/move")
def move_topic(
    topic_id: UUID,
    body: MoveRequest,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    from app.models import Topic
    from app.services.review import record

    topic = db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    if body.parent_id == topic.id:
        raise HTTPException(422, "A topic cannot be its own parent")
    if body.parent_id:
        parent = db.get(Topic, body.parent_id)
        if not parent or parent.chapter_id != topic.chapter_id:
            raise HTTPException(422, "Parent must be in the same chapter")
        ancestor: Topic | None = parent
        seen: set[UUID] = set()
        while ancestor is not None:
            if ancestor.id == topic.id:
                raise HTTPException(422, "A topic cannot be moved under its descendant")
            if ancestor.id in seen:
                raise HTTPException(409, "Existing topic hierarchy contains a cycle")
            seen.add(ancestor.id)
            ancestor = db.get(Topic, ancestor.parent_id) if ancestor.parent_id else None
    before = {
        "parent_id": str(topic.parent_id) if topic.parent_id else None,
        "position": topic.position,
    }
    ordered = list(
        db.scalars(
            select(Topic).where(Topic.chapter_id == topic.chapter_id).order_by(Topic.position)
        ).all()
    )
    ordered.remove(topic)
    if body.position > len(ordered) + 1:
        raise HTTPException(422, "Position exceeds chapter topic count")
    ordered.insert(body.position - 1, topic)
    topic.parent_id, topic.review_status = body.parent_id, "pending"
    from app.services.review import resequence_topics

    resequence_topics(db, ordered)
    record(
        db,
        user,
        "topic",
        topic.id,
        "move",
        before,
        {
            "parent_id": str(topic.parent_id) if topic.parent_id else None,
            "position": topic.position,
        },
    )
    db.commit()
    return {
        "id": str(topic.id),
        "parent_id": str(topic.parent_id) if topic.parent_id else None,
        "position": topic.position,
    }


@router.delete("/topics/{topic_id}", status_code=204)
def delete_topic(
    topic_id: UUID, db: Session = Depends(db_session), user: User = Depends(require_roles("admin"))
) -> None:
    from app.models import Topic
    from app.services.review import record

    topic = db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    record(
        db,
        user,
        "topic",
        topic.id,
        "delete",
        {"title": topic.title, "content_text": topic.content_text},
        None,
    )
    db.delete(topic)
    db.flush()
    survivors = list(
        db.scalars(
            select(Topic).where(Topic.chapter_id == topic.chapter_id).order_by(Topic.position)
        ).all()
    )
    from app.services.review import resequence_topics

    resequence_topics(db, survivors)
    db.commit()


@router.patch("/assets/{asset_id}")
def patch_asset(
    asset_id: UUID,
    body: AssetPatch,
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, Any]:
    from app.models import Asset, Topic
    from app.services.review import record

    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    changes = body.model_dump(exclude_unset=True)
    before = {k: getattr(asset, k) for k in changes}
    if changes.get("review_status") not in {None, "pending", "approved", "needs_fix"}:
        raise HTTPException(422, "Invalid review status")
    for key, value in changes.items():
        if key == "topic_id" and value:
            topic = db.get(Topic, value)
            if not topic or topic.chapter_id != asset.chapter_id:
                raise HTTPException(422, "Topic must belong to the asset chapter")
        setattr(asset, key, value)
    if "review_status" not in changes:
        asset.review_status = "pending"
    record(
        db,
        user,
        "asset",
        asset.id,
        "approve" if changes.get("review_status") == "approved" else "edit",
        before,
        changes,
    )
    db.commit()
    return {
        "id": str(asset.id),
        "caption": asset.caption,
        "alt_text": asset.alt_text,
        "topic_id": str(asset.topic_id) if asset.topic_id else None,
        "position": asset.position,
    }
