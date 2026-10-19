"""Persistent JSON application logs and committed Log-screen events."""
import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session


def redact(value):
    """Remove credential fields recursively before serializing diagnostics."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if re.search(
                r"password|secret|token|authorization|cookie|api.?key", str(key), re.I
            ) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[^\s\"',}]+", "Bearer [REDACTED]", value)
        return re.sub(
            r"(?i)((?:password|secret|[\w-]*token|authorization|cookie|api[_-]?key)"
            r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',}&]+)",
            r"\1[REDACTED]", value,
        )
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "details"):
            payload["details"] = record.details
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str, ensure_ascii=False)


def configure_application_logging(path: str, max_bytes: int, backups: int):
    logger = logging.getLogger("app")
    # Reload/startup must not install duplicate handlers.
    for handler in list(logger.handlers):
        if getattr(handler, "application_file", False):
            logger.removeHandler(handler)
            handler.close()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.application_file = True
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@event.listens_for(Session, "before_flush")
def _collect_log_events(session, flush_context, instances):
    from app.models.browser_import_log import BrowserImportLog

    events = session.info.setdefault("application_log_events", [])
    for row in session.new:
        if isinstance(row, BrowserImportLog):
            events.append(row)


@event.listens_for(Session, "after_commit")
def _write_log_events(session):
    for row in session.info.pop("application_log_events", []):
        details = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        if details["tracking_numbers"]:
            try:
                details["tracking_numbers"] = json.loads(details["tracking_numbers"])
            except (ValueError, TypeError):
                pass
        level = logging.ERROR if details["level"] == "error" else logging.INFO
        logging.getLogger("app.events").log(
            level, details["message"] or details["event_type"], extra={"details": details}
        )


@event.listens_for(Session, "after_rollback")
def _discard_log_events(session):
    session.info.pop("application_log_events", None)
