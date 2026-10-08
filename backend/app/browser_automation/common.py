"""Shared helpers for browser-automation importers."""
from __future__ import annotations

from typing import Any

from app.config import settings


class LoginRequiredError(Exception):
    """Raised when a retailer session is not authenticated."""


def post_bulk_session(orders: list[dict[str, Any]]) -> tuple[str, str]:
    """Create an in-memory bulk import session. Returns (token, review_url)."""
    from app.routers.store_imports import create_bulk_import_session
    from app.schemas.store_import import BulkImportSessionCreate, StoreOrderImportPayload

    payloads = [StoreOrderImportPayload.model_validate(o) for o in orders]
    result = create_bulk_import_session(BulkImportSessionCreate(orders=payloads))
    token = result.token
    base = settings.app_public_base_url.rstrip("/")
    review_url = f"{base}/import-review/bulk?token={token}"
    return token, review_url
