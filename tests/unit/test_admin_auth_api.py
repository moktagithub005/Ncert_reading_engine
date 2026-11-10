from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.admin import auth as auth_module
from app.api.deps import db_session
from app.config import get_settings
from app.core.security import hash_password
from app.main import app
from app.models import User


class AuthSession:
    def __init__(self, user: User) -> None:
        self.user = user

    def scalar(self, _statement):
        return self.user

    def get(self, _model, _user_id):
        return self.user


def test_login_refresh_and_me_flow(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-and-random-enough")
    get_settings.cache_clear()
    user = User(
        id=uuid4(),
        email="admin@example.org",
        password_hash=hash_password("correct-horse-battery-staple"),
        role="admin",
        is_active=True,
    )
    app.dependency_overrides[db_session] = lambda: AuthSession(user)
    monkeypatch.setattr(auth_module, "allow_request", lambda *_args, **_kwargs: True)
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/admin/auth/login",
                json={"email": user.email, "password": "correct-horse-battery-staple"},
            )
            assert login.status_code == 200, login.text
            tokens = login.json()
            refreshed = client.post(
                "/api/v1/admin/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            assert refreshed.status_code == 200
            me = client.get(
                "/api/v1/admin/auth/me",
                headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
            )
        assert me.status_code == 200
        assert me.json()["email"] == user.email
        assert me.json()["role"] == "admin"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_auth_rejects_bad_password_inactive_user_and_invalid_tokens(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-and-random-enough")
    get_settings.cache_clear()
    user = User(
        id=uuid4(),
        email="reviewer@example.org",
        password_hash=hash_password("correct-horse-battery-staple"),
        role="reviewer",
        is_active=True,
    )
    app.dependency_overrides[db_session] = lambda: AuthSession(user)
    monkeypatch.setattr(auth_module, "allow_request", lambda *_args, **_kwargs: True)
    try:
        with TestClient(app) as client:
            wrong_password = client.post(
                "/api/v1/admin/auth/login", json={"email": user.email, "password": "wrong"}
            )
            assert wrong_password.status_code == 401
            user.is_active = False
            inactive_login = client.post(
                "/api/v1/admin/auth/login",
                json={"email": user.email, "password": "correct-horse-battery-staple"},
            )
            invalid_refresh = client.post(
                "/api/v1/admin/auth/refresh", json={"refresh_token": "not-a-jwt"}
            )
            missing_auth = client.get("/api/v1/admin/auth/me")
        assert inactive_login.status_code == 401
        assert invalid_refresh.status_code == 401
        assert missing_auth.status_code == 401
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
