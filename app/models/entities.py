from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FileRecord(Base):
    __tablename__ = "files"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Book(TimestampMixin, Base):
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("class_no", "subject", "language", "edition"),
        CheckConstraint("class_no BETWEEN 1 AND 12"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    class_no: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    edition: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    source_file_id: Mapped[UUID | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class Chapter(TimestampMixin, Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("book_id", "chapter_no"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("chapter_id", "position"),
        Index("ix_topics_ch_parent_position", "chapter_id", "parent_id", "position"),
        Index("ix_topics_search_vector", "search_vector", postgresql_using="gin"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_code: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_blocks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String(24), nullable=False, default="topic")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content_text, ''))",
            persisted=True,
        ),
    )


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("files.id"), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    figure_label: Mapped[str | None] = mapped_column(String(120))
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)


class IngestionJob(TimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
    source_file_id: Mapped[UUID] = mapped_column(ForeignKey("files.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class IngestionStageRun(TimestampMixin, Base):
    __tablename__ = "ingestion_stage_runs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)


class ReviewEvent(Base):
    __tablename__ = "review_events"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    before_data: Mapped[dict[str, Any] | None] = mapped_column("before", JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column("after", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
