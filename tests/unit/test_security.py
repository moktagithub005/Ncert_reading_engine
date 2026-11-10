from uuid import uuid4

import jwt
import pytest

from app.config import get_settings
from app.core.security import create_token, decode_token, hash_password, verify_password


def test_password_hash_round_trip() -> None:
    hashed = hash_password("long-test-password")
    assert hashed != "long-test-password"
    assert verify_password("long-test-password", hashed)
    assert not verify_password("wrong-password", hashed)


def test_access_and_refresh_tokens_are_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-that-is-long-enough")
    get_settings.cache_clear()
    user_id = uuid4()
    access = create_token(user_id, "admin")
    refresh = create_token(user_id, "admin", "refresh")
    assert decode_token(access)["sub"] == str(user_id)
    assert decode_token(refresh, "refresh")["sub"] == str(user_id)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(refresh)
    get_settings.cache_clear()
