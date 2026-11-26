"""Reuse a profile signed in through the BuyingGroup.com portal."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

from anyio import from_thread
from fastapi import HTTPException
from playwright.async_api import Error as BrowserError

from app.browser_automation.session_manager import session_manager
from app.database import SessionLocal
from app.models import BrowserProfile, BuyingGroup
from app.utils.buying_group_apis import buyinggroup
from app.utils.receiving_cache import provider_identity

LOGIN_URL = "https://buyinggroup.com/login"
HOME_URL = "https://buyinggroup.com/home"
SIGN_IN_REQUIRED = "Sign in to BuyingGroup.com from Settings → Buying Groups, then try again."
_profile_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

_SIGNED_IN = """() => {
    if (location.origin !== 'https://buyinggroup.com' || location.pathname.startsWith('/login')) return false;
    const access = localStorage.getItem('auth_token');
    if (!access) return false;
    const expires = Date.parse(localStorage.getItem('token_expiry') || '');
    return !Number.isFinite(expires) || expires > Date.now();
}"""


@dataclass(frozen=True)
class BrowserAccess:
    token: str = field(repr=False)
    identity: str


def supports_browser_session(group: BuyingGroup) -> bool:
    return ((group.api_framework or "").strip().casefold() == "buyinggroup"
            and buyinggroup.resolve_base_url(group.base_url) == buyinggroup.DEFAULT_BASE_URL)


def browser_token_for_group(group: BuyingGroup) -> str:
    """Read the connected account from a FastAPI or scheduler worker thread."""
    access = from_thread.run(browser_access_for_group, group.id)
    if access is None:
        raise HTTPException(409, SIGN_IN_REQUIRED)
    if access.identity != provider_identity(group):
        raise HTTPException(409, "Buying group configuration changed. Try again.")
    return access.token


async def session_logged_in(page) -> bool:
    if urlsplit(page.url).hostname != "buyinggroup.com":
        return False
    return bool(await page.evaluate(_SIGNED_IN))


async def read_signed_in_access(page, expected_account: str, *, previous_access: str | None = None) -> str:
    await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20000)
    try:
        # Let the portal finish restoring or refreshing its own session.
        await page.wait_for_function(
            f"previous => ({_SIGNED_IN})() && (!previous || localStorage.getItem('auth_token') !== previous)",
            arg=previous_access, timeout=12000,
        )
    except BrowserError:
        raise HTTPException(409, SIGN_IN_REQUIRED) from None
    access = await page.evaluate(
        """expected => {
            if (location.origin !== 'https://buyinggroup.com') return null;
            if (expected) {
                let user;
                try { user = JSON.parse(JSON.parse(localStorage.getItem('persist:root'))?.user); }
                catch { return null; }
                const accounts = [user?.email, user?.username].filter(value => typeof value === 'string');
                if (!accounts.some(value => value.trim().toLowerCase() === expected)) return null;
            }
            return localStorage.getItem('auth_token');
        }""", expected_account,
    )
    if not isinstance(access, str) or not access:
        raise HTTPException(409, "Sign in with the BuyingGroup.com account configured for this group.")
    return access


async def browser_access_for_group(group_id: int, *, previous_access: str | None = None) -> BrowserAccess | None:
    """Read a short-lived access token without storing it outside the profile."""
    # Scheduled receiving and payment sync can request access at the same time.
    lock = _profile_locks.setdefault(group_id, asyncio.Lock())
    async with lock:
        return await _read_browser_access(group_id, previous_access=previous_access)


async def _read_browser_access(group_id: int, *, previous_access: str | None) -> BrowserAccess | None:
    with SessionLocal() as db:
        group = db.get(BuyingGroup, group_id)
        if group is None or not supports_browser_session(group):
            return None
        profile = db.query(BrowserProfile).filter_by(buying_group_id=group_id).with_for_update().first()
        if profile is None:
            return None
        if profile.status in ("importing", "login_in_progress"):
            raise HTTPException(409, "Finish the current BuyingGroup.com check or browser sign-in.")
        if profile.status not in ("ready", "error"):
            raise HTTPException(409, SIGN_IN_REQUIRED)
        try:
            buyinggroup.check_request_cooldown(buyinggroup.DEFAULT_BASE_URL)
        except buyinggroup.RateLimited as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from None
        profile_id = profile.id
        identity = provider_identity(group)
        expected_account = (group.api_username or group.api_email or "").strip().casefold()
        profile.status = "importing"
        profile.last_error = None
        db.commit()

    status, error = "ready", None
    try:
        async with asyncio.timeout(40):
            session = await session_manager.ensure_session(profile_id, mode="import", retailer="buyinggroup")
            token = await read_signed_in_access(session.page, expected_account, previous_access=previous_access)
            return BrowserAccess(token, identity)
    except HTTPException as exc:
        status, error = "login_required", exc.detail
        raise
    except (TimeoutError, BrowserError):
        status, error = "error", "Could not open the BuyingGroup.com browser session. Try again or reconnect."
        raise HTTPException(502, error) from None
    finally:
        await session_manager.close_session(profile_id)
        with SessionLocal() as db:
            profile = db.get(BrowserProfile, profile_id)
            if profile is not None and profile.status == "importing":
                profile.status, profile.last_error = status, error
                db.commit()
