from collections.abc import Callable, Generator
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)


def db_session() -> Generator[Session, None, None]:
    yield from get_db()


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(db_session),
) -> User:
    if credentials is None:
        raise HTTPException(401, "Authentication required")
    try:
        payload = decode_token(credentials.credentials)
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired token") from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "User is inactive or missing")
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user

    return dependency
