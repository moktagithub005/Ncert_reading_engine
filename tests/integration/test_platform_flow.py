"""Database-backed acceptance flow; enabled in CI and local integration runs."""

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


def test_upload_review_publish_public_flow(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    email = f"integration-{suffix}@example.com"
    password = "integration-test-password-2026"
    edition = suffix[:12]
    storage_dir = Path(tempfile.mkdtemp(prefix="ncert-integration-"))
    storage = LocalStorage(str(storage_dir))
    book_id: uuid.UUID | None = None
    imported_book_id: uuid.UUID | None = None
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
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
            login = client.post(
                "/api/v1/admin/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            fixture = Path(__file__).parents[1] / "fixtures" / "science_chapter.pdf"
            fixture_copy = storage_dir / f"fixture-{suffix}.pdf"
            fixture_copy.write_bytes(fixture.read_bytes() + f"\n% {suffix}\n".encode())
            with fixture_copy.open("rb") as stream:
                uploaded = client.post(
                    "/api/v1/admin/books/upload",
                    headers=headers,
                    data={
                        "class_no": "6",
                        "subject": "Science",
                        "language": "en",
                        "edition": edition,
                        "reprocess": "true",
                    },
                    files={"file": ("science_chapter.pdf", stream, "application/pdf")},
                )
            assert uploaded.status_code == 202, uploaded.text
            job_id = uuid.UUID(uploaded.json()["job_id"])
            job_response = client.get(f"/api/v1/admin/jobs/{job_id}", headers=headers)
            assert job_response.status_code == 200
            job = job_response.json()
            assert job["status"] == "succeeded", job
            book_id = uuid.UUID(job["book_id"])

            report = client.get(f"/api/v1/admin/jobs/{job_id}/report", headers=headers)
            assert report.status_code == 200
            assert report.json()["chapter_count"] == 1
            tree_response = client.get(
                f"/api/v1/admin/books/{book_id}/tree?include_flags=true", headers=headers
            )
            assert tree_response.status_code == 200
            chapter = tree_response.json()["chapters"][0]
            assert len(chapter["topics"]) >= 2
            topic = chapter["topics"][1]
            split = client.post(
                f"/api/v1/admin/topics/{topic['id']}/split",
                headers=headers,
                json={"character_offset": max(1, len(topic["title"]) // 2)},
            )
            assert split.status_code == 201, split.text
            right_id = split.json()["right_topic_id"]
            merged = client.post(
                "/api/v1/admin/topics/merge",
                headers=headers,
                json={"topic_ids": [topic["id"], right_id]},
            )
            assert merged.status_code == 200, merged.text
            moved = client.post(
                f"/api/v1/admin/topics/{topic['id']}/move",
                headers=headers,
                json={"position": 1, "parent_id": None},
            )
            assert moved.status_code == 200, moved.text
            edited = client.patch(
                f"/api/v1/admin/topics/{topic['id']}",
                headers=headers,
                json={"title": "Reviewed topic"},
            )
            assert edited.status_code == 200, edited.text
            summary = client.get(f"/api/v1/admin/books/{book_id}/review-summary", headers=headers)
            assert summary.status_code == 200
            exported = client.get(f"/api/v1/admin/books/{book_id}/export", headers=headers)
            assert exported.status_code == 200
            round_trip = exported.json()
            round_trip["book"]["edition"] = f"{edition}-roundtrip"
            imported = client.post("/api/v1/admin/books/import", headers=headers, json=round_trip)
            assert imported.status_code == 201, imported.text
            imported_book_id = uuid.UUID(imported.json()["id"])

            tree_response = client.get(
                f"/api/v1/admin/books/{book_id}/tree?include_flags=true", headers=headers
            )
            chapter = tree_response.json()["chapters"][0]
            assert (
                client.post(
                    f"/api/v1/admin/chapters/{chapter['id']}/approve", headers=headers
                ).status_code
                == 200
            )
            for topic in chapter["topics"]:
                approved = client.post(
                    f"/api/v1/admin/topics/{topic['id']}/approve", headers=headers
                )
                assert approved.status_code == 200, approved.text

            published = client.post(f"/api/v1/admin/books/{book_id}/publish", headers=headers)
            assert published.status_code == 200, published.text
            chapters = client.get(f"/api/v1/books/{book_id}/chapters")
            assert chapters.status_code == 200
            chapter_id = chapters.json()["chapters"][0]["id"]
            topics = client.get(f"/api/v1/chapters/{chapter_id}/topics")
            assert topics.status_code == 200
            topic_id = topics.json()["topics"][1]["id"]
            detail = client.get(f"/api/v1/topics/{topic_id}")
            assert detail.status_code == 200
            assert detail.json()["content_text"]
            assert (
                client.get(
                    f"/api/v1/topics/{topic_id}",
                    headers={"If-None-Match": detail.headers["etag"]},
                ).status_code
                == 304
            )
    finally:
        with SessionLocal() as db:
            for target_book_id in (book_id, imported_book_id):
                if not target_book_id:
                    continue
                db.query(ReviewEvent).filter(ReviewEvent.entity_id == target_book_id).delete()
                db.query(Asset).filter(Asset.book_id == target_book_id).delete()
                chapter_ids = [
                    row[0]
                    for row in db.query(Chapter.id).filter(Chapter.book_id == target_book_id).all()
                ]
                db.query(Topic).filter(Topic.chapter_id.in_(chapter_ids)).delete(
                    synchronize_session=False
                )
                db.query(Chapter).filter(Chapter.book_id == target_book_id).delete()
                db.query(Book).filter(Book.id == target_book_id).delete()
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
