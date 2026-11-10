from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.public import content
from app.main import app


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, _ttl, value):
        self.values[key] = value


def test_classes_response_is_cached_and_supports_etag(monkeypatch) -> None:
    cache = FakeRedis()
    monkeypatch.setattr(content, "get_redis", lambda: cache)
    result = Mock()
    result.all.return_value = [6, 8, 10]
    session = Mock()
    session.scalars.return_value = result
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            first = client.get("/api/v1/classes")
            assert first.status_code == 200
            assert first.json() == {"classes": [6, 8, 10]}
            assert first.headers["cache-control"].startswith("public")
            assert first.headers["etag"]
            second = client.get("/api/v1/classes", headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 304
        assert session.scalars.call_count == 1
    finally:
        app.dependency_overrides.clear()
