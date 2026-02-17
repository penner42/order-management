"""Date utilities."""
from datetime import datetime, timezone


def to_date_only(dt: datetime | None) -> datetime | None:
    """Normalize to date-only (time 00:00:00 UTC). Used for shipped, delivered, scanned, etc."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
