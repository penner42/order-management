"""Background loop that runs per-profile full-check / unshipped schedules."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import BrowserProfile
from app.schemas.browser_profile import (
    DEFAULT_FULL_CHECK_CRON,
    DEFAULT_UNSHIPPED_CHECK_CRON,
)

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
    cron_expr: str,
    last_run_at: datetime | None,
    now: datetime,
) -> bool:
    """Return True when a cron schedule should fire.

    Schedules are evaluated in UTC. A run is due when the most recent cron
    fire time is after the last successful run (or when never run).
    """
    if not enabled:
        return False
    expr = (cron_expr or "").strip()
    if not expr or not croniter.is_valid(expr):
        return False
    last = _as_aware(last_run_at)
    if last is None:
        return True
    try:
        # croniter returns naive datetimes when given a naive base; keep UTC.
        base = now.astimezone(timezone.utc).replace(tzinfo=None)
        prev_fire = croniter(expr, base).get_prev(datetime).replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, TypeError):
        logger.warning("Invalid cron expression %r — skipping schedule", expr)
        return False
    return prev_fire > last


def due_modes_for_profile(profile: BrowserProfile, now: datetime | None = None) -> list[str]:
    """Return import modes that are due for this profile (at most one job at a time)."""
    now = now or datetime.now(timezone.utc)
    # Prefer full check when both are due — it covers recent history; unshipped can wait.
    if schedule_due(
        enabled=bool(profile.full_check_enabled),
        cron_expr=str(profile.full_check_cron or DEFAULT_FULL_CHECK_CRON),
        last_run_at=profile.full_check_last_run_at,
        now=now,
    ):
        return ["full"]
    if schedule_due(
        enabled=bool(profile.unshipped_check_enabled),
        cron_expr=str(profile.unshipped_check_cron or DEFAULT_UNSHIPPED_CHECK_CRON),
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
