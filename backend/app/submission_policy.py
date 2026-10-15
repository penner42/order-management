"""Environment policy for writes to real buying-group accounts."""
from fastapi import HTTPException

from app.config import settings


def require_group_submissions_enabled() -> None:
    if not settings.group_submissions_enabled:
        raise HTTPException(status_code=403, detail="Group submissions are disabled in this environment. Lookups and receiving checks remain available.")
