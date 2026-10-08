"""Manage Playwright persistent contexts per browser profile."""
from __future__ import annotations

import asyncio
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
)

# Soften common automation fingerprints before any page JS runs.
# Keep platform/UA honest (real Chrome on Linux) — mismatched Client Hints get flagged.
_STEALTH_INIT_SCRIPT = """
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}
  try {
    if (navigator.webdriver) {
      delete Navigator.prototype.webdriver;
    }
  } catch (e) {}
  try {
    window.chrome = window.chrome || {};
    window.chrome.runtime = window.chrome.runtime || {
      OnInstalledReason: {
        CHROME_UPDATE: 'chrome_update',
        INSTALL: 'install',
        SHARED_MODULE_UPDATE: 'shared_module_update',
        UPDATE: 'update',
      },
      OnRestartRequiredReason: {
        APP_UPDATE: 'app_update',
        OS_UPDATE: 'os_update',
        PERIODIC: 'periodic',
      },
      PlatformArch: {
        ARM: 'arm',
        ARM64: 'arm64',
        MIPS: 'mips',
        MIPS64: 'mips64',
        X86_32: 'x86-32',
        X86_64: 'x86-64',
      },
      PlatformNaclArch: {
        ARM: 'arm',
        MIPS: 'mips',
        MIPS64: 'mips64',
        X86_32: 'x86-32',
        X86_64: 'x86-64',
      },
      PlatformOs: {
        ANDROID: 'android',
        CROS: 'cros',
        LINUX: 'linux',
        MAC: 'mac',
        OPENBSD: 'openbsd',
        WIN: 'win',
      },
      RequestUpdateCheckStatus: {
        NO_UPDATE: 'no_update',
        THROTTLED: 'throttled',
        UPDATE_AVAILABLE: 'update_available',
      },
      connect: function () { return { onMessage: { addListener: function () {} }, postMessage: function () {} }; },
      sendMessage: function () {},
      id: undefined,
    };
    window.chrome.csi = window.chrome.csi || function () { return {}; };
    window.chrome.loadTimes = window.chrome.loadTimes || function () { return {}; };
    window.chrome.app = window.chrome.app || {
      isInstalled: false,
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
      getDetails: function () { return null; },
      getIsInstalled: function () { return false; },
    };
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', {
      get: () => Object.freeze(['en-US', 'en']),
    });
  } catch (e) {}
  try {
    const makePlugin = (name, filename, description) => {
      const plugin = { name, filename, description, length: 1 };
      plugin[0] = { type: 'application/pdf', suffixes: 'pdf', description, enabledPlugin: plugin };
      return plugin;
    };
    const plugins = [
      makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    plugins.item = (i) => plugins[i] || null;
    plugins.namedItem = (n) => plugins.find((p) => p.name === n) || null;
    plugins.refresh = () => {};
    Object.defineProperty(navigator, 'plugins', { get: () => plugins });
    Object.defineProperty(navigator, 'mimeTypes', {
      get: () => {
        const mimes = [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }];
        mimes.item = (i) => mimes[i] || null;
        mimes.namedItem = (n) => mimes.find((m) => m.type === n) || null;
        return mimes;
      },
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
    // Pass common headless checks that look for 0x0 / missing outer sizes.
    if (!window.outerWidth) {
      Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
    }
    if (!window.outerHeight) {
      Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85 });
    }
  } catch (e) {}
})();
"""


def _launch_user_agent() -> str | None:
    """Only override UA when explicitly configured — mismatches with real Chrome get flagged."""
    configured = (settings.browser_user_agent or "").strip()
    return configured or None


DEFAULT_VIEWPORT = {"width": 1440, "height": 900}


@dataclass
class LiveSession:
    profile_id: int
    playwright: Playwright
    context: BrowserContext
    page: Page
    cdp: Any | None = None
    screencast_on: bool = False
    viewers: set[asyncio.Queue] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    mode: str = "idle"  # idle | login | import
    viewport_width: int = DEFAULT_VIEWPORT["width"]
    viewport_height: int = DEFAULT_VIEWPORT["height"]
    channel: str = "chromium"


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

    def _launch_args(self, width: int, height: int) -> list[str]:
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-features=AutomationControlled,IsolateOrigins,site-per-process",
            f"--window-size={width},{height}",
            "--window-position=0,0",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
            "--password-store=basic",
            "--use-mock-keychain",
        ]

    async def _launch_persistent_context(
        self,
        pw: Playwright,
        user_data: str,
        *,
        headless: bool,
        width: int,
        height: int,
    ) -> tuple[BrowserContext, str]:
        """Prefer installed Google Chrome; fall back to Playwright Chromium."""
        common: dict[str, Any] = {
            "headless": headless,
            "viewport": {"width": width, "height": height},
            "screen": {"width": width, "height": height},
            "locale": "en-US",
            "timezone_id": "America/Los_Angeles",
            "color_scheme": "light",
            "args": self._launch_args(width, height),
            "ignore_default_args": ["--enable-automation"],
            # Required inside Docker; real Chrome still behaves much closer to desktop.
            "chromium_sandbox": False,
            "ignore_https_errors": False,
            "java_script_enabled": True,
            "accept_downloads": True,
            "has_touch": False,
            "is_mobile": False,
            "device_scale_factor": 1,
        }
        ua = _launch_user_agent()
        if ua:
            common["user_agent"] = ua

        preferred = (settings.browser_channel or "chrome").strip().lower()
        if preferred in ("", "chromium"):
            ctx = await pw.chromium.launch_persistent_context(user_data, **common)
            return ctx, "chromium"

        try:
            ctx = await pw.chromium.launch_persistent_context(
                user_data,
                channel=preferred,
                **common,
            )
            return ctx, preferred
        except Exception as exc:
            logger.warning(
                "Failed to launch channel=%s (%s); falling back to bundled Chromium",
                preferred,
                exc,
            )
            ctx = await pw.chromium.launch_persistent_context(user_data, **common)
            return ctx, "chromium"

    async def _warm_then_goto(self, page: Page, *, warm_url: str | None, start_url: str) -> None:
        """Hit the retailer homepage first so bot sensors see a normal entry path."""
        if warm_url:
            try:
                await page.goto(warm_url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(900, 2200))
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
                # Walmart/Amazon bot checks (PerimeterX / HUMAN) heavily fingerprint
                # Playwright's bundled Chromium. Prefer real Google Chrome under Xvfb.
                context, channel = await self._launch_persistent_context(
                    pw,
                    user_data,
                    headless=headless,
                    width=width,
                    height=height,
                )
                await context.add_init_script(_STEALTH_INIT_SCRIPT)
                page = context.pages[0] if context.pages else await context.new_page()
                # Extra pass in case a page was created before init scripts attached.
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
                    channel=channel,
                )
                self._sessions[profile_id] = session
                logger.info(
                    "Started browser profile=%s channel=%s headless=%s mode=%s",
                    profile_id,
                    channel,
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
            if session.screencast_on and session.cdp:
                try:
                    await session.cdp.send("Page.stopScreencast")
                except Exception:
                    pass
            await session.context.close()
        except Exception as exc:
            logger.warning("Error closing browser context for profile %s: %s", profile_id, exc)
        try:
            await session.playwright.stop()
        except Exception:
            pass
        self.release_slot()

    async def start_screencast(self, session: LiveSession, queue: asyncio.Queue) -> None:
        async with session.lock:
            session.viewers.add(queue)
            if session.screencast_on:
                return
            cdp = await session.context.new_cdp_session(session.page)
            session.cdp = cdp

            async def on_frame(params: dict) -> None:
                data = params.get("data")
                session_id = params.get("sessionId")
                metadata = params.get("metadata") or {}
                if session.cdp and session_id is not None:
                    try:
                        await session.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                    except Exception:
                        pass
                msg = {
                    "type": "frame",
                    "data": data,
                    "metadata": metadata,
                    "url": session.page.url,
                }
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

            cdp.on("Page.screencastFrame", lambda params: asyncio.create_task(on_frame(params)))
            await cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": 55,
                    "maxWidth": session.viewport_width,
                    "maxHeight": session.viewport_height,
                    "everyNthFrame": 1,
                },
            )
            session.screencast_on = True

    async def stop_viewer(self, session: LiveSession, queue: asyncio.Queue) -> None:
        async with session.lock:
            session.viewers.discard(queue)
            if session.viewers or not session.screencast_on:
                return
            if session.cdp:
                try:
                    await session.cdp.send("Page.stopScreencast")
                except Exception:
                    pass
            session.screencast_on = False

    async def resize_viewport(self, session: LiveSession, width: int, height: int) -> None:
        """Match Playwright viewport + screencast to the embedded window size."""
        width = max(320, min(3840, int(width)))
        height = max(240, min(2160, int(height)))
        # Keep even dimensions (some sensors dislike odd sizes).
        width -= width % 2
        height -= height % 2
        if width == session.viewport_width and height == session.viewport_height:
            return
        async with session.lock:
            session.viewport_width = width
            session.viewport_height = height
            try:
                await session.page.set_viewport_size({"width": width, "height": height})
            except Exception as exc:
                logger.warning("set_viewport_size failed: %s", exc)
            cdp = session.cdp
            if cdp:
                try:
                    await cdp.send(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                            "screenWidth": width,
                            "screenHeight": height,
                        },
                    )
                except Exception:
                    pass
            if not session.screencast_on or not session.cdp:
                return
            try:
                await session.cdp.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                await session.cdp.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": 55,
                        "maxWidth": width,
                        "maxHeight": height,
                        "everyNthFrame": 1,
                    },
                )
            except Exception as exc:
                logger.warning("Restart screencast after resize failed: %s", exc)
                session.screencast_on = False

    async def dispatch_input(self, session: LiveSession, message: dict) -> None:
        """Forward mouse/keyboard events from the UI into CDP."""
        cdp = session.cdp
        if not cdp:
            cdp = await session.context.new_cdp_session(session.page)
            session.cdp = cdp

        msg_type = message.get("type")
        if msg_type == "mouse":
            event = message.get("event") or "click"
            x = float(message.get("x") or 0)
            y = float(message.get("y") or 0)
            button = message.get("button") or "left"
            click_count = int(message.get("clickCount") or 1)
            modifiers = int(message.get("modifiers") or 0)
            if event == "move":
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": x, "y": y, "modifiers": modifiers},
                )
            elif event == "down":
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mousePressed",
                        "x": x,
                        "y": y,
                        "button": button,
                        "clickCount": click_count,
                        "modifiers": modifiers,
                    },
                )
            elif event == "up":
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": button,
                        "clickCount": click_count,
                        "modifiers": modifiers,
                    },
                )
            elif event == "wheel":
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "x": x,
                        "y": y,
                        "deltaX": float(message.get("deltaX") or 0),
                        "deltaY": float(message.get("deltaY") or 0),
                        "modifiers": modifiers,
                    },
                )
            elif event == "click":
                for etype in ("mousePressed", "mouseReleased"):
                    await cdp.send(
                        "Input.dispatchMouseEvent",
                        {
                            "type": etype,
                            "x": x,
                            "y": y,
                            "button": button,
                            "clickCount": click_count,
                            "modifiers": modifiers,
                        },
                    )
        elif msg_type == "key":
            event = message.get("event") or "down"
            key = message.get("key") or ""
            code = message.get("code") or ""
            text = message.get("text")
            modifiers = int(message.get("modifiers") or 0)
            windows_virtual_key_code = message.get("windowsVirtualKeyCode")
            native_virtual_key_code = message.get("nativeVirtualKeyCode")
            payload: dict[str, Any] = {
                "type": "keyDown" if event == "down" else ("keyUp" if event == "up" else "char"),
                "modifiers": modifiers,
            }
            if key:
                payload["key"] = key
            if code:
                payload["code"] = code
            if text is not None:
                payload["text"] = text
            if windows_virtual_key_code is not None:
                payload["windowsVirtualKeyCode"] = int(windows_virtual_key_code)
            if native_virtual_key_code is not None:
                payload["nativeVirtualKeyCode"] = int(native_virtual_key_code)
            # char events need text
            if payload["type"] == "char" and not payload.get("text"):
                payload["text"] = key if len(key) == 1 else ""
            await cdp.send("Input.dispatchKeyEvent", payload)


session_manager = SessionManager()


def looks_like_amazon_signin(url: str) -> bool:
    u = (url or "").lower()
    return any(hint in u for hint in AMAZON_SIGNIN_HINTS)


async def amazon_session_logged_in(page: Page) -> bool:
    url = page.url or ""
    if looks_like_amazon_signin(url):
        return False
    try:
        # Nav account line or orders chrome is a strong signal.
        handle = await page.query_selector("#nav-link-accountList, #nav-orders, .nav-action-signin-label")
        if handle:
            text = (await handle.inner_text()) or ""
            if "sign in" in text.lower():
                return False
        if "/your-orders" in url or "order-history" in url:
            return not looks_like_amazon_signin(url)
        # Try loading orders; redirect to signin means logged out.
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
        # Sign-in CTA on orders page means logged out.
        sign_in = await page.query_selector(
            'a[href*="login"], button:has-text("Sign in"), a:has-text("Sign in")'
        )
        if sign_in:
            href = (await sign_in.get_attribute("href")) or ""
            text = ((await sign_in.inner_text()) or "").lower()
            if "login" in href.lower() or "sign in" in text:
                # If we also see order detail links, still treat as logged in.
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
