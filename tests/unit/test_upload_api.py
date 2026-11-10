from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.admin import upload as upload_module
from app.api.deps import current_user, db_session
from app.main import app
from app.models import FileRecord, IngestionJob, User
from app.storage.local import LocalStorage

FIXTURE = Path(__file__).parents[1] / "fixtures" / "science_chapter.pdf"


class UploadSession:
    def __init__(self) -> None:
        self.pending = []

    def scalar(self, _statement):
        return None

    def add(self, value) -> None:
        self.pending.append(value)

    def flush(self) -> None:
        for value in self.pending:
            if isinstance(value, FileRecord) and value.id is None:
                value.id = uuid4()

    def commit(self) -> None:
        pass

    def refresh(self, value) -> None:
        if isinstance(value, IngestionJob) and value.id is None:
            value.id = uuid4()


def test_upload_valid_pdf_persists_job_and_queues_worker(monkeypatch, tmp_path) -> None:
    session = UploadSession()
    user = User(email="admin@example.test", password_hash="unused", role="admin", is_active=True)
    app.dependency_overrides[db_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: user
    monkeypatch.setattr(upload_module, "allow_request", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(upload_module, "get_storage", lambda: LocalStorage(str(tmp_path)))
    queued = Mock()
    monkeypatch.setattr(upload_module.process_book, "delay", queued)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/admin/books/upload",
                files={"file": ("science.pdf", FIXTURE.read_bytes(), "application/pdf")},
                data={"class_no": "8", "subject": "Science", "language": "en", "edition": "demo"},
            )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert queued.called
        assert any(isinstance(item, FileRecord) for item in session.pending)
        assert any(isinstance(item, IngestionJob) for item in session.pending)
    finally:
        app.dependency_overrides.clear()


def test_upload_rejects_non_pdf_before_storage(monkeypatch) -> None:
    session = UploadSession()
    app.dependency_overrides[db_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: User(
        email="admin@example.test", password_hash="unused", role="admin", is_active=True
    )
    monkeypatch.setattr(upload_module, "allow_request", lambda *_args, **_kwargs: True)
    storage = Mock()
    monkeypatch.setattr(upload_module, "get_storage", lambda: storage)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/admin/books/upload",
                files={"file": ("bad.pdf", b"not a pdf", "application/pdf")},
                data={"class_no": "8", "subject": "Science", "language": "en", "edition": "demo"},
            )
        assert response.status_code == 415
        storage.put.assert_not_called()
    finally:
        app.dependency_overrides.clear()
