import argparse

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import User

parser = argparse.ArgumentParser(description="Create the first NCERT platform admin")
parser.add_argument("--email", required=True)
parser.add_argument("--password", required=True)
args = parser.parse_args()
if len(args.password) < 12:
    parser.error("Password must be at least 12 characters")
with SessionLocal() as session:
    if session.scalar(select(User).where(User.role == "admin")):
        parser.error("An admin user already exists")
    session.add(
        User(
            email=args.email,
            password_hash=hash_password(args.password),
            role="admin",
            is_active=True,
        )
    )
    session.commit()
print(f"Created admin {args.email}")
