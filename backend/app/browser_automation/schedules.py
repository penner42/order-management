"""Persist staggered store checks and claim each run before starting a browser."""
from datetime import datetime, timedelta, timezone

from app.buying_group_schedules import utc
from app.models import BrowserProfile

SCHEDULE_FIELDS = frozenset({
    "full_check_enabled", "unshipped_check_enabled", "unshipped_check_interval_minutes",
})
MODES = ("full", "unshipped")


def interval(profile, mode):
    return timedelta(days=1) if mode == "full" else timedelta(minutes=profile.unshipped_check_interval_minutes)


def rebuild_schedules(db, now=None):
    rows = db.query(BrowserProfile).filter(BrowserProfile.integration.is_(None)).order_by(BrowserProfile.id).with_for_update().all()
    active = []
    for profile in rows:
        for mode in MODES:
            setattr(profile, f"{mode}_check_next_run_at", None)
            if getattr(profile, f"{mode}_check_enabled"):
                active.append((profile, mode))
    if not active:
        return
    now = utc(now or datetime.now(timezone.utc))
    window = min(interval(profile, mode) for profile, mode in active)
    for index, (profile, mode) in enumerate(active):
        setattr(profile, f"{mode}_check_next_run_at", now + window * ((index + 1) / len(active)))


def list_due_profile_jobs(db, now=None):
    now = utc(now or datetime.now(timezone.utc))
    rows = db.query(BrowserProfile).filter(BrowserProfile.integration.is_(None)).order_by(BrowserProfile.id).all()
    if any(getattr(p, f"{mode}_check_enabled") and getattr(p, f"{mode}_check_next_run_at") is None
           for p in rows for mode in MODES):
        rebuild_schedules(db, now)
        db.commit()
    jobs = []
    for profile in rows:
        if profile.status != "ready":
            continue
        for mode in MODES:
            due = getattr(profile, f"{mode}_check_next_run_at")
            if getattr(profile, f"{mode}_check_enabled") and due is not None and utc(due) <= now:
                jobs.append((profile.id, mode, profile.full_check_max_pages if mode == "full" else 3))
                break
    return jobs


def claim_profile_job(db, profile_id, mode, now=None):
    if mode not in MODES:
        return None
    now = utc(now or datetime.now(timezone.utc))
    profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).with_for_update().first()
    if not profile or profile.integration or profile.status != "ready" or not getattr(profile, f"{mode}_check_enabled"):
        db.rollback()
        return None
    field = f"{mode}_check_next_run_at"
    due = getattr(profile, field)
    if due is None or utc(due) > now:
        db.rollback()
        return None
    cadence = interval(profile, mode)
    setattr(profile, field, utc(due) + cadence * ((now - utc(due)) // cadence + 1))
    db.commit()
    return profile
