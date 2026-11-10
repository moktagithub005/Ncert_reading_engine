"""Run synthetic layout fixtures through the real ingestion pipeline."""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.admin import auth as auth_api
from app.api.admin import upload as upload_api
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.ingestion import pipeline
from app.main import app
from app.models import (
    Asset,
    Book,
    Chapter,
    FileRecord,
    IngestionJob,
    IngestionStageRun,
    ReviewEvent,
    Topic,
    User,
)
from app.storage.local import LocalStorage
from app.workers.tasks import process_book

pytestmark = pytest.mark.skipif(
    os.environ.get("NCERT_INTEGRATION_TESTS") != "1",
    reason="Set NCERT_INTEGRATION_TESTS=1 to run against Postgres and Redis",
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
CASES = [
    ("science_chapter", "science_chapter", "Science", "en"),
    ("maths_chapter", "maths_chapter", "Mathematics", "en"),
    ("hindi_chapter", "hindi_chapter", "विज्ञान", "hi"),
    ("two_column_chapter", "two_column_chapter", "Science", "en"),
    ("scanned_page", "scanned_page", "Science", "en"),
]


@pytest.mark.parametrize(("fixture_name", "expected_key", "subject", "language"), CASES)
def test_layout_fixture_pipeline_output(
    fixture_name: str, expected_key: str, subject: str, language: str, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex
    email = f"golden-{suffix}@example.com"
    password = "golden-test-password-2026"
    edition = suffix[:12]
    storage_dir = Path(tempfile.mkdtemp(prefix="ncert-golden-"))
    storage = LocalStorage(str(storage_dir))
    book_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    monkeypatch.setattr(auth_api, "allow_request", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(upload_api, "allow_request", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(upload_api, "get_storage", lambda: storage)
    monkeypatch.setattr(pipeline, "get_storage", lambda: storage)
    monkeypatch.setattr(
        process_book,
        "delay",
        lambda job: process_book.apply(args=(job,), throw=True),
    )
    try:
        with SessionLocal() as db:
            user = User(
                email=email,
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
            )
            db.add(user)
            db.commit()
            user_id = user.id

        with TestClient(app) as client:
            login = client.post(
                "/api/v1/admin/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            original_pdf = FIXTURES / f"{fixture_name}.pdf"
            fixture_copy = storage_dir / f"{suffix}.pdf"
            fixture_copy.write_bytes(original_pdf.read_bytes() + f"\n% {suffix}\n".encode())
            with fixture_copy.open("rb") as stream:
                uploaded = client.post(
                    "/api/v1/admin/books/upload",
                    headers=headers,
                    data={
                        "class_no": "6",
                        "subject": subject,
                        "language": language,
                        "edition": edition,
                    },
                    files={"file": (original_pdf.name, stream, "application/pdf")},
                )
            assert uploaded.status_code == 202, uploaded.text
            job_id = uuid.UUID(uploaded.json()["job_id"])
            job = client.get(f"/api/v1/admin/jobs/{job_id}", headers=headers).json()
            assert job["status"] == "succeeded", job
            book_id = uuid.UUID(job["book_id"])
            tree = client.get(
                f"/api/v1/admin/books/{book_id}/tree?include_flags=true", headers=headers
            ).json()
            expected = EXPECTED[expected_key]
            assert len(tree["chapters"]) == expected["chapters"]
            detected = [
                topic["title"] for chapter in tree["chapters"] for topic in chapter["topics"]
            ]
            for expected_title in expected["topics"]:
                assert expected_title in detected, (expected_title, detected)
            if "ocr_pages" in expected:
                report = client.get(f"/api/v1/admin/jobs/{job_id}/report", headers=headers).json()
                assert report["ocr_pages"] == expected["ocr_pages"]
    finally:
        with SessionLocal() as db:
            if book_id:
                db.query(ReviewEvent).filter(ReviewEvent.entity_id == book_id).delete()
                db.query(Asset).filter(Asset.book_id == book_id).delete()
                chapter_ids = [
                    row[0] for row in db.query(Chapter.id).filter(Chapter.book_id == book_id).all()
                ]
                db.query(Topic).filter(Topic.chapter_id.in_(chapter_ids)).delete(
                    synchronize_session=False
                )
                db.query(Chapter).filter(Chapter.book_id == book_id).delete()
                db.query(Book).filter(Book.id == book_id).delete()
            if job_id:
                db.query(IngestionStageRun).filter(IngestionStageRun.job_id == job_id).delete()
                source_id = (
                    db.query(IngestionJob.source_file_id).filter(IngestionJob.id == job_id).scalar()
                )
                db.query(IngestionJob).filter(IngestionJob.id == job_id).delete()
                if source_id:
                    db.query(FileRecord).filter(FileRecord.id == source_id).delete()
            if user_id:
                db.query(User).filter(User.id == user_id).delete()
            db.commit()
        shutil.rmtree(storage_dir, ignore_errors=True)
