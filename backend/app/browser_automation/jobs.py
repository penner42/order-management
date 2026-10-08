"""In-memory job registry for browser automation (login/import)."""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class BrowserJob:
    id: str
    profile_id: int
    kind: str  # login | import
    status: str = "queued"  # queued | running | succeeded | failed | cancelled
    message: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    review_url: str | None = None
    token: str | None = None
    order_count: int | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "progress": self.progress or None,
            "review_url": self.review_url,
            "token": self.token,
            "order_count": self.order_count,
            "error": self.error,
        }


_lock = threading.Lock()
_jobs: dict[str, BrowserJob] = {}


def create_job(profile_id: int, kind: str) -> BrowserJob:
    job = BrowserJob(id=secrets.token_urlsafe(12), profile_id=profile_id, kind=kind)
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> BrowserJob | None:
    with _lock:
        return _jobs.get(job_id)


def update_job(job_id: str, **kwargs: Any) -> BrowserJob | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.touch()
        return job
