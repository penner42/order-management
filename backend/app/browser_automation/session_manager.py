"""Manage Playwright persistent contexts per browser profile (Firefox)."""
from __future__ import annotations

import asyncio
import base64
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.browser_automation.paths import profile_user_data_dir
from app.config import settings

logger = logging.getLogger(__name__)

AMAZON_HOME_URL = "https://www.amazon.com/"
AMAZON_ORDERS_URL = "https://www.amazon.com/your-orders/orders?disableCsd=missing-library"
AMAZON_SIGNIN_HINTS = ("/ap/signin", "/ap/mfa", "/ap/cvf")

WALMART_HOME_URL = "https://www.walmart.com/"
WALMART_ORDERS_URL = "https://www.walmart.com/orders"
WALMART_SIGNIN_HINTS = (
    "/account/login",
    "/login",
    "identity.walmart.com",
    "/signin",
    "/authorize",
    "px-captcha",
    "human.walmart",
    "/blocked",
)

# Soften common automation fingerprints before any page JS runs.
# Keep platform/UA honest — mismatched fingerprints get flagged.
_STEALTH_INIT_SCRIPT = """
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}
  try {
    if (Object.getOwnPropertyDescriptor(navigator, 'webdriver')) {
      delete navigator.webdriver;
    }
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', {
      get: () => Object.freeze(['en-US', 'en']),
    });
  } catch (e) {}
  try {
    const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (originalQuery) {
      window.navigator.permissions.query = (parameters) =>
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters);
    }
  } catch (e) {}
  try {
    if (!window.outerWidth) {
      Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
    }
    if (!window.outerHeight) {
      Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85 });
    }
  } catch (e) {}
})();
"""

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
# Screencast max bounds — keep stable so resize does not restart the stream.
SCREENCAST_MAX = {"width": 1920, "height": 1080}
RESIZE_SNAP = 16
RESIZE_THRESHOLD = 16
# Reject tiny panes (e.g. 1120×240) that look automated and get hard-blocked.
MIN_VIEWPORT = {"width": 1024, "height": 640}


def _snap_size(width: int, height: int) -> tuple[int, int]:
    width = max(MIN_VIEWPORT["width"], min(SCREENCAST_MAX["width"], int(width)))
    height = max(MIN_VIEWPORT["height"], min(SCREENCAST_MAX["height"], int(height)))
    width = max(MIN_VIEWPORT["width"], (width // RESIZE_SNAP) * RESIZE_SNAP)
    height = max(MIN_VIEWPORT["height"], (height // RESIZE_SNAP) * RESIZE_SNAP)
    return width, height


def _launch_user_agent() -> str | None:
    """Only override UA when explicitly configured."""
    configured = (settings.browser_user_agent or "").strip()
    return configured or None


def _playwright_key(key: str) -> str | None:
    """Map a browser KeyboardEvent.key to a Playwright key name."""
    if not key or key in ("Dead", "Unidentified", "Process"):
        return None
    # Playwright accepts standard KeyboardEvent.key values for most keys.
    return key


@dataclass
class LiveSession:
    profile_id: int
    playwright: Playwright
    context: BrowserContext
    page: Page
    screencast_on: bool = False
    screencast_handle: Any | None = None
    viewers: set[asyncio.Queue] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    mode: str = "idle"  # idle | login | import
    viewport_width: int = DEFAULT_VIEWPORT["width"]
    viewport_height: int = DEFAULT_VIEWPORT["height"]
    browser: str = "firefox"


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[int, LiveSession] = {}
        self._slot = asyncio.Semaphore(max(1, settings.browser_max_concurrent))
        self._global_lock = asyncio.Lock()

    async def acquire_slot(self) -> None:
        await self._slot.acquire()

    def release_slot(self) -> None:
        self._slot.release()

    def get(self, profile_id: int) -> LiveSession | None:
        return self._sessions.get(profile_id)

    async def _launch_persistent_context(
        self,
        pw: Playwright,
        user_data: str,
        *,
        headless: bool,
        width: int,
        height: int,
    ) -> BrowserContext:
        kwargs: dict[str, Any] = {
            "headless": headless,
            "viewport": {"width": width, "height": height},
            "locale": "en-US",
            "timezone_id": "America/Los_Angeles",
            "color_scheme": "light",
            "ignore_https_errors": False,
            "java_script_enabled": True,
            "accept_downloads": True,
            "has_touch": False,
            "firefox_user_prefs": {
                # Reduce first-run / automation nags
                "browser.shell.checkDefaultBrowser": False,
                "datareporting.policy.dataSubmissionEnabled": False,
                "toolkit.telemetry.reportingpolicy.firstRun": False,
                "dom.webnotifications.enabled": False,
            },
        }
        ua = _launch_user_agent()
        if ua:
            kwargs["user_agent"] = ua
        return await pw.firefox.launch_persistent_context(user_data, **kwargs)

    async def _warm_then_goto(self, page: Page, *, warm_url: str | None, start_url: str) -> None:
        """Hit the retailer homepage first so bot sensors see a normal entry path."""
        current = (page.url or "").lower()
        if "/blocked" in current:
            logger.warning("Skipping navigation; page is already on a block interstitial: %s", page.url)
            return
        if warm_url:
            try:
                await page.goto(warm_url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(900, 2200))
                if "/blocked" in (page.url or "").lower():
                    logger.warning("Blocked during warm navigation: %s", page.url)
                    return
            except Exception as exc:
                logger.warning("Warm navigation to %s failed: %s", warm_url, exc)
        try:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            logger.warning("Navigate to %s failed: %s", start_url, exc)

    async def ensure_session(
        self,
        profile_id: int,
        *,
        mode: str,
        start_url: str | None = None,
        warm_url: str | None = None,
    ) -> LiveSession:
        async with self._global_lock:
            existing = self._sessions.get(profile_id)
            if existing:
                existing.mode = mode
                if start_url:
                    await self._warm_then_goto(existing.page, warm_url=warm_url, start_url=start_url)
                return existing

            await self.acquire_slot()
            try:
                user_data = str(profile_user_data_dir(profile_id))
                pw = await async_playwright().start()
                headless = bool(settings.browser_headless)
                width = DEFAULT_VIEWPORT["width"]
                height = DEFAULT_VIEWPORT["height"]
                context = await self._launch_persistent_context(
                    pw,
                    user_data,
                    headless=headless,
                    width=width,
                    height=height,
                )
                await context.add_init_script(_STEALTH_INIT_SCRIPT)
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.add_init_script(_STEALTH_INIT_SCRIPT)
                except Exception:
                    pass
                session = LiveSession(
                    profile_id=profile_id,
                    playwright=pw,
                    context=context,
                    page=page,
                    mode=mode,
                    viewport_width=width,
                    viewport_height=height,
                    browser="firefox",
                )
                self._sessions[profile_id] = session
                logger.info(
                    "Started browser profile=%s browser=firefox headless=%s mode=%s",
                    profile_id,
                    headless,
                    mode,
                )
                if start_url:
                    await self._warm_then_goto(page, warm_url=warm_url, start_url=start_url)
                return session
            except Exception:
                self.release_slot()
                raise

    async def close_session(self, profile_id: int) -> None:
        async with self._global_lock:
            session = self._sessions.pop(profile_id, None)
        if not session:
            return
        try:
            if session.screencast_on:
                try:
                    await session.page.screencast.stop()
                except Exception:
                    pass
                session.screencast_on = False
                session.screencast_handle = None
            await session.context.close()
        except Exception as exc:
            logger.warning("Error closing browser context for profile %s: %s", profile_id, exc)
        try:
            await session.playwright.stop()
        except Exception:
            pass
        self.release_slot()

    def _broadcast_frame(self, session: LiveSession, msg: dict) -> None:
        dead: list[asyncio.Queue] = []
        for q in list(session.viewers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    dead.append(q)
        for q in dead:
            session.viewers.discard(q)

    def _on_frame_handler(self, session: LiveSession):
        async def on_frame(frame: dict) -> None:
            data = frame.get("data")
            if not data:
                return
            if isinstance(data, (bytes, bytearray)):
                b64 = base64.b64encode(data).decode("ascii")
            else:
                b64 = str(data)
            msg = {
                "type": "frame",
                "data": b64,
                "metadata": {
                    "viewportWidth": frame.get("viewportWidth") or session.viewport_width,
                    "viewportHeight": frame.get("viewportHeight") or session.viewport_height,
                    "timestamp": frame.get("timestamp"),
                },
                "url": session.page.url,
            }
            self._broadcast_frame(session, msg)

        return on_frame

    async def _start_screencast_locked(self, session: LiveSession) -> None:
        """Caller must hold session.lock. Starts Playwright page.screencast (Firefox-safe)."""
        session.screencast_handle = await session.page.screencast.start(
            on_frame=self._on_frame_handler(session),
            quality=55,
            # Fixed max size — do not tie to viewport or every resize restarts the stream.
            size=dict(SCREENCAST_MAX),
        )
        session.screencast_on = True

    async def _stop_screencast_locked(self, session: LiveSession) -> None:
        try:
            await session.page.screencast.stop()
        except Exception:
            pass
        session.screencast_on = False
        session.screencast_handle = None

    async def start_screencast(self, session: LiveSession, queue: asyncio.Queue) -> None:
        async with session.lock:
            session.viewers.add(queue)
            if session.screencast_on:
                return
            await self._start_screencast_locked(session)

    async def stop_viewer(self, session: LiveSession, queue: asyncio.Queue) -> None:
        async with session.lock:
            session.viewers.discard(queue)
            if session.viewers or not session.screencast_on:
                return
            await self._stop_screencast_locked(session)

    async def resize_viewport(self, session: LiveSession, width: int, height: int) -> None:
        """Match Playwright viewport to the embedded window size (no screencast restart)."""
        width, height = _snap_size(width, height)
        prev_w, prev_h = session.viewport_width, session.viewport_height
        if width == prev_w and height == prev_h:
            return
        if prev_w > 0 and prev_h > 0:
            if abs(width - prev_w) < RESIZE_THRESHOLD and abs(height - prev_h) < RESIZE_THRESHOLD:
                return
        async with session.lock:
            if width == session.viewport_width and height == session.viewport_height:
                return
            session.viewport_width = width
            session.viewport_height = height
            try:
                await session.page.set_viewport_size({"width": width, "height": height})
            except Exception as exc:
                logger.warning("set_viewport_size failed: %s", exc)

    async def dispatch_input(self, session: LiveSession, message: dict) -> None:
        """Forward mouse/keyboard events from the UI into Playwright (Firefox-safe)."""
        page = session.page
        msg_type = message.get("type")
        if msg_type == "mouse":
            event = message.get("event") or "click"
            x = float(message.get("x") or 0)
            y = float(message.get("y") or 0)
            button = message.get("button") or "left"
            if button not in ("left", "right", "middle"):
                button = "left"
            if event == "move":
                await page.mouse.move(x, y)
            elif event == "down":
                await page.mouse.move(x, y)
                await page.mouse.down(button=button)
            elif event == "up":
                await page.mouse.move(x, y)
                await page.mouse.up(button=button)
            elif event == "wheel":
                await page.mouse.move(x, y)
                await page.mouse.wheel(
                    float(message.get("deltaX") or 0),
                    float(message.get("deltaY") or 0),
                )
            elif event == "click":
                click_count = int(message.get("clickCount") or 1)
                await page.mouse.click(x, y, button=button, click_count=click_count)
        elif msg_type == "key":
            event = message.get("event") or "down"
            key = _playwright_key(str(message.get("key") or ""))
            if not key:
                return
            # Skip separate "char" events — keyboard.down already inserts text and
            # sending both doubles every character (same bug we fixed for CDP).
            if event == "char":
                return
            try:
                if event == "down":
                    await page.keyboard.down(key)
                elif event == "up":
                    await page.keyboard.up(key)
            except Exception as exc:
                # Unknown key names should not kill the live session.
                logger.debug("keyboard %s %r failed: %s", event, key, exc)


session_manager = SessionManager()


def looks_like_amazon_signin(url: str) -> bool:
    u = (url or "").lower()
    return any(hint in u for hint in AMAZON_SIGNIN_HINTS)


async def amazon_session_logged_in(page: Page) -> bool:
    url = page.url or ""
    if looks_like_amazon_signin(url):
        return False
    try:
        handle = await page.query_selector("#nav-link-accountList, #nav-orders, .nav-action-signin-label")
        if handle:
            text = (await handle.inner_text()) or ""
            if "sign in" in text.lower():
                return False
        if "/your-orders" in url or "order-history" in url:
            return not looks_like_amazon_signin(url)
        await page.goto(AMAZON_ORDERS_URL, wait_until="domcontentloaded", timeout=45_000)
        return not looks_like_amazon_signin(page.url)
    except Exception:
        return False


def looks_like_walmart_signin(url: str) -> bool:
    u = (url or "").lower()
    return any(hint in u for hint in WALMART_SIGNIN_HINTS)


async def walmart_session_logged_in(page: Page) -> bool:
    url = page.url or ""
    if looks_like_walmart_signin(url):
        return False
    try:
        await page.goto(WALMART_ORDERS_URL, wait_until="domcontentloaded", timeout=45_000)
        if looks_like_walmart_signin(page.url):
            return False
        sign_in = await page.query_selector(
            'a[href*="login"], button:has-text("Sign in"), a:has-text("Sign in")'
        )
        if sign_in:
            href = (await sign_in.get_attribute("href")) or ""
            text = ((await sign_in.inner_text()) or "").lower()
            if "login" in href.lower() or "sign in" in text:
                order_link = await page.query_selector('a[href*="/orders/"]')
                if not order_link:
                    return False
        return "/orders" in (page.url or "").lower()
    except Exception:
        return False


def login_start_url_for_retailer(retailer: str) -> str:
    if retailer == "walmart":
        return WALMART_ORDERS_URL
    return AMAZON_ORDERS_URL


def login_warm_url_for_retailer(retailer: str) -> str:
    if retailer == "walmart":
        return WALMART_HOME_URL
    return AMAZON_HOME_URL


async def retailer_session_logged_in(page: Page, retailer: str) -> bool:
    if retailer == "walmart":
        return await walmart_session_logged_in(page)
    return await amazon_session_logged_in(page)
