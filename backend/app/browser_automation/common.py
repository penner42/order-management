"""Shared helpers for browser-automation importers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from playwright.async_api import Page

from app.config import settings

# store_order_number, error message
OrderErrorCallback = Callable[[str, str], None]


class LoginRequiredError(Exception):
    """Raised when a retailer session is not authenticated."""


async def inject_scripts_for_evaluate(page: Page, paths: Sequence[Path]) -> None:
    """Load extension scripts into the same JS world as ``page.evaluate``.

    Camoufox runs Playwright evaluate in an isolated world. ``add_script_tag``
    installs into the page main world, so helpers like ``OrderManagerWalmart``
    are invisible to later evaluates. Running the file source via evaluate keeps
    them in the same world.
    """
    for path in paths:
        source = path.read_text(encoding="utf-8")
        await page.evaluate("(code) => { (0, eval)(code); }", source)


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
