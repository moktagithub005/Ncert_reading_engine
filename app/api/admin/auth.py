from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.config import get_settings
from app.core.rate_limit import allow_request
from app.core.security import create_token, decode_token, verify_password
from app.models import User

router = APIRouter(prefix="/auth", tags=["admin-auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
def login(
    body: LoginRequest, request: Request, db: Session = Depends(db_session)
) -> dict[str, str]:
    client = request.client.host if request.client else "unknown"
    settings = get_settings()
    if not allow_request(
        f"rate:login:{client}:{body.email.lower()}", settings.login_rate_limit_per_minute
    ):
        raise HTTPException(429, "Login rate limit exceeded")
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {
        "access_token": create_token(user.id, user.role),
        "refresh_token": create_token(user.id, user.role, "refresh"),
        "token_type": "bearer",
    }


@router.post("/refresh")
def refresh(body: RefreshRequest, db: Session = Depends(db_session)) -> dict[str, str]:
    try:
        payload = decode_token(body.refresh_token, "refresh")
        user = db.get(User, UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired refresh token") from None
    if user is None or not user.is_active:
        raise HTTPException(401, "User is inactive or missing")
    return {
        "access_token": create_token(user.id, user.role),
        "refresh_token": create_token(user.id, user.role, "refresh"),
        "token_type": "bearer",
    }


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict[str, str | UUID]:
    return {"id": user.id, "email": user.email, "role": user.role}
