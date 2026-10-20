"""Background loop that runs per-profile full-check / unshipped schedules."""
from __future__ import annotations

import asyncio
import logging

from app.database import SessionLocal
from app.browser_automation.schedules import list_due_profile_jobs

logger = logging.getLogger(__name__)

# How often the loop wakes to look for due schedules.
TICK_SECONDS = 1

_task: asyncio.Task | None = None
_lock = asyncio.Lock()


async def _tick() -> None:
    # Import here to avoid circular imports at module load.
    from app.routers.browser_profiles import start_scheduled_import
    from app.buying_group_schedules import list_due_group_ids
    from app.routers.buying_groups import run_scheduled_group
    from app.routers.monarch import scheduled_tick

    await scheduled_tick()

    db = SessionLocal()
    try:
        due = list_due_profile_jobs(db)
        due_groups = list_due_group_ids(db)
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

    for group_id in due_groups:
        try:
            started = await asyncio.to_thread(run_scheduled_group, group_id)
            if started:
                logger.info(
                    "Ran scheduled jobs for buying group %s",
                    group_id,
                )
        except Exception:
            logger.exception(
                "Failed scheduled jobs for buying group %s",
                group_id,
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
