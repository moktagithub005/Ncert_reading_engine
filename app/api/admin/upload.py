import hashlib
import os
import tempfile
from collections.abc import Iterator
from typing import Any, cast

import pymupdf as fitz
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_roles
from app.config import get_settings
from app.core.rate_limit import allow_request
from app.core.text import remove_nul_characters
from app.models import FileRecord, IngestionJob, User
from app.storage import get_storage
from app.workers.tasks import process_book

router = APIRouter(tags=["admin-ingestion"])


@router.post("/books/upload", status_code=202)
def upload_book(
    request: Request,
    file: UploadFile = File(...),
    class_no: int = Form(...),
    subject: str = Form(...),
    language: str = Form(...),
    edition: str = Form(...),
    reprocess: bool = Form(False),
    db: Session = Depends(db_session),
    user: User = Depends(require_roles("admin", "reviewer")),
) -> dict[str, str]:
    settings = get_settings()
    client = request.client.host if request.client else "unknown"
    if not allow_request(f"rate:upload:{client}", settings.upload_rate_limit_per_minute):
        raise HTTPException(429, "Upload rate limit exceeded")
    if (
        not 1 <= class_no <= 12
        or language not in {"en", "hi"}
        or not subject.strip()
        or not edition.strip()
    ):
        raise HTTPException(422, "Invalid book metadata")
    digest, total = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temp:
        while chunk := file.file.read(1024 * 1024):
            total += len(chunk)
            if total > settings.max_upload_mb * 1024 * 1024:
                raise HTTPException(413, "PDF exceeds configured size limit")
            digest.update(chunk)
            temp.write(chunk)
        if total < 5:
            raise HTTPException(422, "File is not a valid PDF")
        temp.flush()
        temp.seek(0)
        if temp.read(5) != b"%PDF-":
            raise HTTPException(415, "Only PDF files are accepted")
        try:
            # PyMuPDF does not publish complete static types for its Document API.
            doc = cast(Any, fitz.open(temp.name))  # type: ignore[no-untyped-call]
            page_count = doc.page_count
            encrypted = doc.is_encrypted
            doc.close()
        except Exception as exc:
            raise HTTPException(422, f"PDF cannot be opened: {exc}") from exc
        if encrypted:
            raise HTTPException(422, "Encrypted PDFs are not supported")
        if page_count > settings.max_pages:
            raise HTTPException(413, f"PDF exceeds maximum of {settings.max_pages} pages")
        sha = digest.hexdigest()
        existing_file = db.scalar(select(FileRecord).where(FileRecord.sha256 == sha))
        if existing_file:
            existing_job = db.scalar(
                select(IngestionJob)
                .where(IngestionJob.source_file_id == existing_file.id)
                .order_by(IngestionJob.id.desc())
            )
            if existing_job and not reprocess:
                return {
                    "job_id": str(existing_job.id),
                    "status": existing_job.status,
                    "deduplicated": "true",
                }
            file_record = existing_file
        else:
            key = f"sources/{sha[:2]}/{sha}.pdf"
            temp.seek(0)

            def chunks() -> Iterator[bytes]:
                while data := temp.read(1024 * 1024):
                    yield data

            stored_size = get_storage().put(key, chunks(), "application/pdf")
            file_record = FileRecord(
                storage_key=key,
                original_name=remove_nul_characters(os.path.basename(file.filename or "book.pdf")),
                mime_type="application/pdf",
                size_bytes=stored_size,
                sha256=sha,
                kind="source_pdf",
            )
            db.add(file_record)
            db.flush()
        job = IngestionJob(
            source_file_id=file_record.id,
            status="queued",
            params={
                "class_no": class_no,
                "subject": subject.strip(),
                "language": language,
                "edition": edition.strip(),
                "options": {},
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)
    process_book.delay(str(job.id))
    return {"job_id": str(job.id), "status": "queued"}
