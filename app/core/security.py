from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.config import get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def create_token(subject: UUID, role: str, token_type: str = "access") -> str:
    settings = get_settings()
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET must be configured")
    now = datetime.now(UTC)
    ttl = (
        timedelta(minutes=settings.jwt_access_ttl_minutes)
        if token_type == "access"
        else timedelta(days=settings.jwt_refresh_ttl_days)
    )
    return jwt.encode(
        {"sub": str(subject), "role": role, "type": token_type, "iat": now, "exp": now + ttl},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload
