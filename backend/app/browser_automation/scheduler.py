"""Background loop that runs per-profile full-check / unshipped schedules."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import BrowserProfile

logger = logging.getLogger(__name__)

# How often the loop wakes to look for due schedules.
TICK_SECONDS = 60

_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def schedule_due(
    *,
    enabled: bool,
    interval_hours: int,
    last_run_at: datetime | None,
    now: datetime,
) -> bool:
    if not enabled:
        return False
    hours = max(1, int(interval_hours or 1))
    last = _as_aware(last_run_at)
    if last is None:
        return True
    return last + timedelta(hours=hours) <= now


def due_modes_for_profile(profile: BrowserProfile, now: datetime | None = None) -> list[str]:
    """Return import modes that are due for this profile (at most one job at a time)."""
    now = now or datetime.now(timezone.utc)
    # Prefer full check when both are due — it covers recent history; unshipped can wait.
    if schedule_due(
        enabled=bool(profile.full_check_enabled),
        interval_hours=int(profile.full_check_interval_hours or 24),
        last_run_at=profile.full_check_last_run_at,
        now=now,
    ):
        return ["full"]
    if schedule_due(
        enabled=bool(profile.unshipped_check_enabled),
        interval_hours=int(profile.unshipped_check_interval_hours or 6),
        last_run_at=profile.unshipped_check_last_run_at,
        now=now,
    ):
        return ["unshipped"]
    return []


def list_due_profile_jobs(db: Session) -> list[tuple[int, str, int]]:
    """Return (profile_id, mode, max_pages) for profiles that should run now."""
    now = datetime.now(timezone.utc)
    # Only ready profiles — avoid spinning browsers every minute when login is needed.
    rows = (
        db.query(BrowserProfile)
        .filter(BrowserProfile.status == "ready")
        .order_by(BrowserProfile.id)
        .all()
    )
    out: list[tuple[int, str, int]] = []
    for profile in rows:
        modes = due_modes_for_profile(profile, now)
        if not modes:
            continue
        mode = modes[0]
        max_pages = int(profile.full_check_max_pages or 3) if mode == "full" else 3
        out.append((profile.id, mode, max_pages))
    return out


async def _tick() -> None:
    # Import here to avoid circular imports at module load.
    from app.routers.browser_profiles import start_scheduled_import

    db = SessionLocal()
    try:
        due = list_due_profile_jobs(db)
    finally:
        db.close()

    for profile_id, mode, max_pages in due:
        try:
            started = await start_scheduled_import(profile_id, mode=mode, max_pages=max_pages)
            if started:
                logger.info(
                    "Started scheduled %s check for browser profile %s (max_pages=%s)",
                    mode,
                    profile_id,
                    max_pages,
                )
        except Exception:
            logger.exception(
                "Failed to start scheduled %s check for browser profile %s",
                mode,
                profile_id,
            )


async def _loop() -> None:
    logger.info("Browser automation schedule loop started (tick=%ss)", TICK_SECONDS)
    # Small delay so the API can finish booting before the first tick.
    await asyncio.sleep(5)
    while True:
        try:
            async with _lock:
                await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Browser automation schedule tick failed")
        await asyncio.sleep(TICK_SECONDS)


def start_scheduler() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="browser-automation-scheduler")


async def stop_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
