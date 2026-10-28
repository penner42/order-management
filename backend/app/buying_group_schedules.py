"""Persist and claim staggered buying group interval schedules."""
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import BuyingGroup

SCHEDULE_FIELDS = frozenset({"tracking_submit_enabled", "received_check_enabled", "payment_sync_enabled", "schedule_interval_minutes"})


def payment_enabled(group):
    if not getattr(group, "payment_sync_enabled", False):
        return False
    from app.payment_providers import provider_for
    provider = provider_for(group)
    return bool(provider and provider.ready(group)[0])


def utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def enabled(group):
    return payment_enabled(group) or group.received_check_enabled or (group.tracking_submit_enabled and settings.group_submissions_enabled)


def stagger_groups(groups, now):
    """Spread active groups across the shortest interval, then repeat at their cadence."""
    active = []
    for group in sorted(groups, key=lambda group: group.id):
        group.schedule_next_run_at = None
        if group.tracking_submit_enabled or group.received_check_enabled or payment_enabled(group):
            active.append(group)
    if not active:
        return
    window = min(g.schedule_interval_minutes for g in active) * 60
    for index, group in enumerate(active):
        group.schedule_next_run_at = utc(now) + timedelta(seconds=window * (index + 1) / len(active))


def rebuild_schedules(db, now=None):
    rows = db.query(BuyingGroup).order_by(BuyingGroup.id).with_for_update().all()
    stagger_groups(rows, now or datetime.now(timezone.utc))


def list_due_group_ids(db, now=None):
    now = now or datetime.now(timezone.utc)
    rows = db.query(BuyingGroup).order_by(BuyingGroup.id).all()
    if any(enabled(g) and g.schedule_next_run_at is None for g in rows):
        rebuild_schedules(db, now)
        db.commit()
    return [g.id for g in rows if enabled(g) and g.schedule_next_run_at is not None
            and utc(g.schedule_next_run_at) <= utc(now)]


def claim_group_job(db, group_id, now=None):
    """Advance before external work so errors and restarts cannot cause retry storms."""
    now = utc(now or datetime.now(timezone.utc))
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).with_for_update().first()
    if not group or not enabled(group):
        db.rollback()
        return None
    due = group.schedule_next_run_at
    if due is None or utc(due) > now:
        db.rollback()
        return None
    interval = timedelta(minutes=group.schedule_interval_minutes)
    due = utc(due)
    group.schedule_next_run_at = due + interval * ((now - due) // interval + 1)
    db.commit()
    return group
