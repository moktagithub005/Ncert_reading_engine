from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def test_health_and_openapi_endpoints_are_available() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        schema = client.get("/openapi.json").json()
        assert "/api/v1/topics/{topic_id}" in schema["paths"]


def test_ready_returns_503_when_a_dependency_is_down(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("database offline")

    monkeypatch.setattr(main_module.engine, "connect", unavailable)
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_request_body_limit_rejects_oversized_payload(monkeypatch) -> None:
    monkeypatch.setattr(main_module.settings, "body_size_limit_bytes", 10)
    with TestClient(app) as client:
        response = client.post("/health", content=b"01234567890")
    assert response.status_code == 413
