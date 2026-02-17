"""Ensure admin user exists and password matches env (create or update)."""
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import settings
from app.models import User

USER_ROLE_ADMIN = "admin"


def ensure_admin_user(db: Session) -> None:
    admin = db.query(User).filter(User.username == settings.admin_username).first()
    hashed = hash_password(settings.admin_password)
    if admin:
        admin.hashed_password = hashed
        admin.role = USER_ROLE_ADMIN
        db.commit()
        return
    admin = User(
        username=settings.admin_username,
        email=None,
        name="Admin",
        role=USER_ROLE_ADMIN,
        hashed_password=hashed,
    )
    db.add(admin)
    db.commit()
