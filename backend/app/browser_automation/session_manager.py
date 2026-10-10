"""Manage Camoufox (anti-detect Firefox) persistent contexts per browser profile."""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import BrowserContext, Page

from app.browser_automation.paths import browser_engine_for_retailer, profile_user_data_dir
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

COSTCO_HOME_URL = "https://www.costco.com/"
COSTCO_MYACCOUNT_URL = "https://www.costco.com/myaccount/"
# Only real SSO hosts / LogonForm. Do NOT match OAuthLogonCmd — that is the
# successful post-login callback on www.costco.com (substring "/logon"/"oauthlogon").
COSTCO_SIGNIN_HOST_HINTS = (
    "signin.costco.com",
    "signin-ui.costco.com",
)
COSTCO_SIGNIN_PATH_HINTS = (
    "/logonform",
)

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
# Cap stream resolution for WebSocket bandwidth; keep close to the UI window.
SCREENCAST_MAX = {"width": 2560, "height": 1440}
SCREENCAST_QUALITY = 72
RESIZE_SNAP = 16
RESIZE_THRESHOLD = 16
MIN_VIEWPORT = {"width": 1024, "height": 720}


def _snap_size(width: int, height: int) -> tuple[int, int]:
    width = max(MIN_VIEWPORT["width"], min(SCREENCAST_MAX["width"], int(width)))
    height = max(MIN_VIEWPORT["height"], min(SCREENCAST_MAX["height"], int(height)))
    width = max(MIN_VIEWPORT["width"], (width // RESIZE_SNAP) * RESIZE_SNAP)
    height = max(MIN_VIEWPORT["height"], (height // RESIZE_SNAP) * RESIZE_SNAP)
    return width, height


def _playwright_key(key: str) -> str | None:
    """Map a browser KeyboardEvent.key to a Playwright key name."""
    if not key or key in ("Dead", "Unidentified", "Process"):
        return None
    return key


@dataclass
class LiveSession:
    profile_id: int
    context: BrowserContext
    page: Page
    camoufox: Any = None  # AsyncCamoufox CM when browser == camoufox
    playwright: Any = None  # Playwright driver when browser == chromium
    screencast_on: bool = False
    screencast_handle: Any | None = None
    viewers: set[asyncio.Queue] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    mode: str = "idle"  # idle | login | import
    viewport_width: int = DEFAULT_VIEWPORT["width"]
    viewport_height: int = DEFAULT_VIEWPORT["height"]
    browser: str = "camoufox"  # camoufox | chromium
    retailer: str | None = None
    # Coalesce pointer moves so the WS receive loop never waits on humanize/animation.
    pending_mouse: tuple[float, float] | None = None
    mouse_flush_task: asyncio.Task | None = None
    # Serialize move flush vs down/up so coalesced moves can't displace clicks.
    mouse_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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

    async def _launch_camoufox_context(
        self,
        user_data: str,
        *,
        headless: bool,
        width: int,
        height: int,
    ) -> tuple[BrowserContext, Any]:
        """Launch Camoufox persistent context (anti-detect Firefox fork)."""
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise RuntimeError(
                "camoufox is not installed. Rebuild the backend image after adding the dependency."
            ) from exc

        # headless=False under our Xvfb — avoid headless="virtual" (1×1 display is detectable).
        # Sandbox env vars are set in Docker (MOZ_DISABLE_*_SANDBOX) for LXC/EPERM.
        # humanize=False: live-view mouse must be immediate (humanize adds ~1s per move).
        cm = AsyncCamoufox(
            persistent_context=True,
            user_data_dir=user_data,
            headless=bool(headless),
            os="linux",
            locale="en-US",
            humanize=False,
            enable_cache=True,
            # Needed for predictable live-view click mapping; Camoufox still spoofs other signals.
            window=(width, height),
            block_webrtc=True,
            # Camoufox ≥156.0.1-beta.32 renamed disableInstantAnimations → instantAnimations.
            # When true, finite CSS animations complete instantly (hides press-and-hold /
            # B2C challenge UI). Keep false so Costco/Azure login animations paint.
            config={"instantAnimations": False},
            firefox_user_prefs={
                "security.sandbox.content.level": 0,
                "security.sandbox.gpu.level": 0,
                # Azure B2C SSO sets cookies on signin.costco.com then posts back
                # to www.costco.com — strict tracking protection can stall that hop.
                "network.cookie.cookieBehavior": 0,
                "network.cookie.cookieBehavior.optInPartitioning": False,
            },
        )
        context = await cm.__aenter__()
        if not isinstance(context, BrowserContext):
            # Non-persistent mode returns a Browser; we always request persistent_context.
            raise RuntimeError("Camoufox did not return a persistent BrowserContext")
        return context, cm

    async def _launch_chromium_context(
        self,
        user_data: str,
        *,
        headless: bool,
        width: int,
        height: int,
    ) -> tuple[BrowserContext, Any]:
        """Launch Chromium persistent context (Costco Azure B2C / Amazon automations)."""
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data,
                headless=bool(headless),
                viewport={"width": width, "height": height},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                # Soften the default automation banner; Costco still sees a real Chrome TLS stack.
                ignore_default_args=["--enable-automation"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    f"--window-size={width},{height}",
                ],
            )
        except Exception:
            await pw.stop()
            raise
        return context, pw

    async def _warm_then_goto(self, page: Page, *, warm_url: str | None, start_url: str) -> None:
        """Optional warm path used for import jobs (not interactive login)."""
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
        retailer: str | None = None,
    ) -> LiveSession:
        async with self._global_lock:
            existing = self._sessions.get(profile_id)
            if existing:
                existing.mode = mode
                if retailer:
                    existing.retailer = retailer
                if start_url:
                    await self._warm_then_goto(existing.page, warm_url=warm_url, start_url=start_url)
                return existing

            await self.acquire_slot()
            try:
                engine = browser_engine_for_retailer(retailer or "")
                user_data = str(profile_user_data_dir(profile_id, browser=engine))
                headless = bool(settings.browser_headless)
                # Login/live view: open at stream cap so set_viewport_size can grow
                # without being clipped. Import jobs use a smaller window to save RAM/CPU.
                if mode == "login":
                    width = SCREENCAST_MAX["width"]
                    height = SCREENCAST_MAX["height"]
                else:
                    width = MIN_VIEWPORT["width"]
                    height = MIN_VIEWPORT["height"]

                camoufox = None
                playwright = None
                if engine == "chromium":
                    context, playwright = await self._launch_chromium_context(
                        user_data,
                        headless=headless,
                        width=width,
                        height=height,
                    )
                else:
                    context, camoufox = await self._launch_camoufox_context(
                        user_data,
                        headless=headless,
                        width=width,
                        height=height,
                    )

                page = context.pages[0] if context.pages else await context.new_page()
                # Sync viewport from whatever the browser actually opened.
                try:
                    vp = page.viewport_size
                    if vp:
                        width = int(vp.get("width") or width)
                        height = int(vp.get("height") or height)
                except Exception:
                    pass
                session = LiveSession(
                    profile_id=profile_id,
                    context=context,
                    page=page,
                    camoufox=camoufox,
                    playwright=playwright,
                    mode=mode,
                    viewport_width=width,
                    viewport_height=height,
                    browser=engine,
                    retailer=retailer,
                )
                self._sessions[profile_id] = session
                self._attach_page_listeners(session)
                logger.info(
                    "Started browser profile=%s browser=%s retailer=%s headless=%s mode=%s",
                    profile_id,
                    engine,
                    retailer or "?",
                    headless,
                    mode,
                )
                # Interactive login: stay on blank/new tab — user navigates in the live view.
                # Import jobs pass start_url and still warm-navigate.
                if start_url and mode != "login":
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
            if session.camoufox is not None:
                try:
                    await session.camoufox.__aexit__(None, None, None)
                except Exception as exc:
                    logger.warning("Camoufox shutdown failed for profile %s: %s", profile_id, exc)
                    try:
                        await session.context.close()
                    except Exception:
                        pass
            else:
                try:
                    await session.context.close()
                except Exception as exc:
                    logger.debug("Chromium context close failed for profile %s: %s", profile_id, exc)
                if session.playwright is not None:
                    try:
                        await session.playwright.stop()
                    except Exception as exc:
                        logger.debug("Playwright stop failed for profile %s: %s", profile_id, exc)
        except Exception as exc:
            logger.warning("Error closing browser context for profile %s: %s", profile_id, exc)
        self.release_slot()

    def _attach_page_listeners(self, session: LiveSession) -> None:
        """Follow SSO popups / new tabs so live view isn't stuck on a dimmed opener."""

        def on_page(page: Page) -> None:
            try:
                asyncio.create_task(self._adopt_page(session, page))
            except Exception as exc:
                logger.debug("Failed to schedule page adopt: %s", exc)

        try:
            session.context.on("page", on_page)
        except Exception as exc:
            logger.debug("context.on(page) failed: %s", exc)

    async def _adopt_page(self, session: LiveSession, page: Page) -> None:
        if session.page is page:
            return
        async with session.lock:
            if session.page is page:
                return
            old = session.page
            logger.info(
                "Adopting new browser page for profile %s (was %s)",
                session.profile_id,
                (old.url if old else "")[:120],
            )
            try:
                if session.screencast_on:
                    await self._stop_screencast_locked(session)
            except Exception as exc:
                logger.debug("stop screencast before adopt failed: %s", exc)
            session.page = page
            try:
                if session.viewers:
                    await self._start_screencast_locked(session)
            except Exception as exc:
                logger.warning("restart screencast after adopt failed: %s", exc)
            # Best-effort: bring popup to front for input focus.
            try:
                await page.bring_to_front()
            except Exception:
                pass

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
            # Playwright's onFrame viewport* is the page CSS size at capture time.
            # JPEG may be smaller (scaled to fit `size`); the UI must map via these.
            vp_w = int(frame.get("viewportWidth") or 0)
            vp_h = int(frame.get("viewportHeight") or 0)
            if vp_w < 200 or vp_h < 200:
                try:
                    vp = session.page.viewport_size
                    if vp and vp.get("width") and vp.get("height"):
                        vp_w = int(vp["width"])
                        vp_h = int(vp["height"])
                except Exception:
                    pass
            if vp_w < 200 or vp_h < 200:
                vp_w = session.viewport_width
                vp_h = session.viewport_height
            else:
                session.viewport_width = vp_w
                session.viewport_height = vp_h
            msg = {
                "type": "frame",
                "data": b64,
                "metadata": {
                    "viewportWidth": vp_w,
                    "viewportHeight": vp_h,
                    "timestamp": frame.get("timestamp"),
                },
                "url": session.page.url,
            }
            self._broadcast_frame(session, msg)

        return on_frame

    async def _start_screencast_locked(self, session: LiveSession) -> None:
        """Caller must hold session.lock."""
        # Size is a max bound; Firefox scales frames down to fit while preserving
        # aspect ratio. Prefer matching the live viewport so frames stay 1:1 when possible.
        cast_w = min(SCREENCAST_MAX["width"], max(MIN_VIEWPORT["width"], session.viewport_width))
        cast_h = min(SCREENCAST_MAX["height"], max(MIN_VIEWPORT["height"], session.viewport_height))
        session.screencast_handle = await session.page.screencast.start(
            on_frame=self._on_frame_handler(session),
            quality=SCREENCAST_QUALITY,
            size={"width": cast_w, "height": cast_h},
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
        """Match Playwright viewport to the embedded window; restart screencast if size changed."""
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
            try:
                await session.page.set_viewport_size({"width": width, "height": height})
            except Exception as exc:
                logger.warning("set_viewport_size failed: %s", exc)
            # Trust the size Playwright actually applied (Camoufox/window may clamp).
            try:
                vp = session.page.viewport_size
                if vp and vp.get("width") and vp.get("height"):
                    width = int(vp["width"])
                    height = int(vp["height"])
            except Exception:
                pass
            session.viewport_width = width
            session.viewport_height = height
            # Restart stream so JPEG size tracks the new viewport (sharper when larger).
            if session.screencast_on and session.viewers:
                await self._stop_screencast_locked(session)
                await self._start_screencast_locked(session)

    async def _flush_pending_mouse(self, session: LiveSession) -> None:
        """Apply the latest coalesced pointer position (drops intermediate moves)."""
        try:
            async with session.mouse_lock:
                while session.pending_mouse is not None:
                    x, y = session.pending_mouse
                    session.pending_mouse = None
                    try:
                        await session.page.mouse.move(x, y)
                    except Exception as exc:
                        logger.debug("mouse move failed: %s", exc)
                        break
        except asyncio.CancelledError:
            raise
        finally:
            session.mouse_flush_task = None

    def _queue_mouse_move(self, session: LiveSession, x: float, y: float) -> None:
        session.pending_mouse = (x, y)
        task = session.mouse_flush_task
        if task is None or task.done():
            session.mouse_flush_task = asyncio.create_task(self._flush_pending_mouse(session))

    async def _mouse_action(self, session: LiveSession, x: float, y: float, action) -> None:
        """Run a click-related mouse op without racing coalesced moves."""
        session.pending_mouse = None
        async with session.mouse_lock:
            await action(x, y)

    async def _snap_to_nearby_input(self, page: Page, x: float, y: float) -> tuple[float, float]:
        """Widen hit targets for tiny OTP/digit inputs (Walmart MFA, etc.).

        Never steal clicks from buttons/links near text fields — Costco Azure B2C
        puts Send code / Sign in immediately beside inputs; snapping those clicks
        into the field makes login look like a hung overlay.
        """
        try:
            snapped = await page.evaluate(
                """([x, y]) => {
                  const MAX_DIST = 28;
                  const isTypeable = (el) => {
                    if (!el || el.disabled || el.readOnly) return false;
                    const tag = (el.tagName || '').toLowerCase();
                    if (tag === 'textarea') return true;
                    if (tag !== 'input') return false;
                    const type = (el.type || 'text').toLowerCase();
                    return !['hidden', 'checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'image'].includes(type);
                  };
                  const isClickable = (el) => {
                    if (!el || el === document.body || el === document.documentElement) return false;
                    const tag = (el.tagName || '').toLowerCase();
                    if (['button', 'a', 'summary', 'label'].includes(tag)) return true;
                    const role = (el.getAttribute && el.getAttribute('role')) || '';
                    if (['button', 'link', 'tab', 'menuitem'].includes(role)) return true;
                    if (tag === 'input') {
                      const type = (el.type || 'text').toLowerCase();
                      if (['button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'image'].includes(type)) {
                        return true;
                      }
                    }
                    try {
                      if (el.onclick != null) return true;
                    } catch (e) {}
                    return false;
                  };
                  const center = (el) => {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return null;
                    return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height, r };
                  };
                  const at = document.elementFromPoint(x, y);
                  let el = at;
                  while (el) {
                    if (isTypeable(el)) {
                      const c = center(el);
                      if (c) return { x: c.x, y: c.y };
                    }
                    // Clicking Sign in / Send code / social buttons must not retarget.
                    if (isClickable(el)) return null;
                    el = el.parentElement;
                  }
                  // Visual OTP boxes are often wrappers; the real input may sit under/near them.
                  const inputs = Array.from(document.querySelectorAll('input, textarea'));
                  let best = null;
                  let bestDist = MAX_DIST;
                  for (const inp of inputs) {
                    if (!isTypeable(inp)) continue;
                    const c = center(inp);
                    if (!c) continue;
                    const cx = Math.max(c.r.left, Math.min(x, c.r.right));
                    const cy = Math.max(c.r.top, Math.min(y, c.r.bottom));
                    const dist = Math.hypot(x - cx, y - cy);
                    // Prefer snapping into narrow digit cells even when the click is slightly off.
                    const narrow = c.w < 48 || c.h < 48;
                    const limit = narrow ? MAX_DIST : 12;
                    if (dist <= limit && dist < bestDist) {
                      bestDist = dist;
                      best = { x: c.x, y: c.y };
                    }
                  }
                  return best;
                }""",
                [x, y],
            )
            if isinstance(snapped, dict) and "x" in snapped and "y" in snapped:
                return float(snapped["x"]), float(snapped["y"])
        except Exception as exc:
            logger.debug("input snap failed: %s", exc)
        return x, y

    async def _paste_into_focused(self, page: Page, text: str) -> None:
        """Paste via insert_text, then nudge Angular/React controlled inputs."""
        await page.keyboard.insert_text(text)
        try:
            await page.evaluate(
                """() => {
                  const el = document.activeElement;
                  if (!el) return;
                  const tag = (el.tagName || '').toLowerCase();
                  if (tag !== 'input' && tag !== 'textarea') return;
                  const proto =
                    tag === 'textarea'
                      ? window.HTMLTextAreaElement.prototype
                      : window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  const value = el.value;
                  if (desc && typeof desc.set === 'function') {
                    desc.set.call(el, value);
                  }
                  try {
                    el.dispatchEvent(
                      new InputEvent('input', {
                        bubbles: true,
                        cancelable: true,
                        inputType: 'insertFromPaste',
                        data: value,
                      })
                    );
                  } catch (e) {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                  }
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
        except Exception as exc:
            logger.debug("paste SPA nudge failed: %s", exc)

    async def dispatch_input(self, session: LiveSession, message: dict) -> None:
        """Forward mouse/keyboard events from the UI into Playwright."""
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
                # Non-blocking: coalesce so a backlog of moves can't freeze the live view.
                self._queue_mouse_move(session, x, y)
            elif event == "down":
                x, y = await self._snap_to_nearby_input(page, x, y)

                async def _down(px: float, py: float) -> None:
                    await page.mouse.move(px, py)
                    await page.mouse.down(button=button)

                await self._mouse_action(session, x, y, _down)
            elif event == "up":
                # Don't re-target on up: down may have snapped into a tiny OTP cell;
                # moving again would drag-focus away from it.
                session.pending_mouse = None

                async def _up(_px: float, _py: float) -> None:
                    await page.mouse.up(button=button)

                await self._mouse_action(session, x, y, _up)
            elif event == "wheel":

                async def _wheel(px: float, py: float) -> None:
                    await page.mouse.move(px, py)
                    await page.mouse.wheel(
                        float(message.get("deltaX") or 0),
                        float(message.get("deltaY") or 0),
                    )

                await self._mouse_action(session, x, y, _wheel)
            elif event == "click":
                click_count = int(message.get("clickCount") or 1)
                x, y = await self._snap_to_nearby_input(page, x, y)

                async def _click(px: float, py: float) -> None:
                    await page.mouse.click(px, py, button=button, click_count=click_count)

                await self._mouse_action(session, x, y, _click)
        elif msg_type == "key":
            event = message.get("event") or "down"
            key = _playwright_key(str(message.get("key") or ""))
            if not key:
                return
            if event == "char":
                # Use type() so React/Angular (Costco Azure B2C) see real keydown/input/keyup.
                # insert_text alone often fills the DOM without updating the SPA model, so
                # Sign In spins forever. Fall back to insert_text for odd OTP widgets.
                text = str(message.get("text") or key)
                if text:
                    try:
                        await page.keyboard.type(text, delay=0)
                    except Exception as exc:
                        logger.debug("keyboard type %r failed: %s", text, exc)
                        try:
                            await page.keyboard.insert_text(text)
                        except Exception as exc2:
                            logger.debug("keyboard insert_text %r failed: %s", text, exc2)
                return
            try:
                if event == "down":
                    # Printable keys are applied via the separate char/type event.
                    if len(key) == 1:
                        return
                    await page.keyboard.down(key)
                elif event == "up":
                    if len(key) == 1:
                        return
                    await page.keyboard.up(key)
            except Exception as exc:
                logger.debug("keyboard %s %r failed: %s", event, key, exc)
        elif msg_type == "paste":
            text = str(message.get("text") or "")
            if not text:
                return
            # Cap absurd pastes (passwords/MFA codes are tiny; bulk paste still ok).
            if len(text) > 100_000:
                text = text[:100_000]
            try:
                # insert_text is one CDP round-trip (type() on a whole password stalls
                # the live-view loop and often looks like paste "did nothing").
                await self._paste_into_focused(page, text)
            except Exception as exc:
                logger.debug("paste failed: %s", exc)


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
    """Probe /orders to verify the session — do not trust the live-view URL alone.

    After a successful login the live view often still sits on identity.walmart.com
    or an authorize redirect, which matches sign-in URL hints. Also, Walmart pages
    keep generic "Sign in" links in the DOM even when logged in, and empty order
    history has no /orders/ detail links — so DOM heuristics false-negative easily.
    """
    try:
        await page.goto(WALMART_ORDERS_URL, wait_until="domcontentloaded", timeout=45_000)
        # SPA may client-redirect to login after first paint.
        for _ in range(10):
            if looks_like_walmart_signin(page.url or ""):
                return False
            if "/orders" in (page.url or "").lower():
                return True
            await asyncio.sleep(0.4)
        url = (page.url or "").lower()
        if looks_like_walmart_signin(url):
            return False
        return "/orders" in url
    except Exception:
        return False


def looks_like_costco_signin(url: str) -> bool:
    """True only when the browser is on Costco's real SSO UI — not OAuth callbacks."""
    u = (url or "").lower()
    if any(host in u for host in COSTCO_SIGNIN_HOST_HINTS):
        return True
    # OAuthLogonCmd is the success redirect; never treat it as signed-out.
    if "oauthlogon" in u:
        return False
    return any(path in u for path in COSTCO_SIGNIN_PATH_HINTS)


async def establish_costco_session(page: Page, *, timeout_s: float = 25.0) -> bool:
    """Home → myaccount so cookie SSO can hydrate before we judge login state.

    Costco often still paints a Sign In affordance on first paint; going home
    first then myaccount lets the session establish. Returns True when the SPA
    lands on /myaccount/#/app/... .
    """
    try:
        await page.goto(COSTCO_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(1500)
        await page.goto(COSTCO_MYACCOUNT_URL, wait_until="domcontentloaded", timeout=60_000)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            url = page.url or ""
            lower = url.lower()
            # Logged-in SPA
            if "/myaccount" in lower and "#/app/" in url:
                return True
            # Still on silent SSO / OAuth callback — keep waiting.
            if "oauthlogon" in lower or (
                looks_like_costco_signin(url) and "authorize" in lower
            ):
                await asyncio.sleep(0.4)
                continue
            # Settled on interactive SSO (email/password) → not logged in.
            if looks_like_costco_signin(url):
                # Give redirects a moment; only fail if we stay on SSO.
                await asyncio.sleep(0.8)
                if looks_like_costco_signin(page.url or "") and "#/app/" not in (page.url or ""):
                    return False
                continue
            await asyncio.sleep(0.4)
        url = page.url or ""
        return "/myaccount" in url.lower() and "#/app/" in url
    except Exception:
        return False


async def costco_session_logged_in(page: Page) -> bool:
    """Probe via home → myaccount (cookie SSO needs the homepage hop)."""
    return await establish_costco_session(page)


def login_start_url_for_retailer(retailer: str) -> str:
    if retailer == "walmart":
        return WALMART_ORDERS_URL
    if retailer == "costco":
        return COSTCO_MYACCOUNT_URL
    return AMAZON_ORDERS_URL


def login_warm_url_for_retailer(retailer: str) -> str:
    if retailer == "walmart":
        return WALMART_HOME_URL
    if retailer == "costco":
        return COSTCO_HOME_URL
    return AMAZON_HOME_URL


def login_home_url_for_retailer(retailer: str) -> str:
    if retailer == "walmart":
        return WALMART_HOME_URL
    if retailer == "costco":
        return COSTCO_HOME_URL
    return AMAZON_HOME_URL


async def retailer_session_logged_in(page: Page, retailer: str) -> bool:
    if retailer == "walmart":
        return await walmart_session_logged_in(page)
    if retailer == "costco":
        return await costco_session_logged_in(page)
    return await amazon_session_logged_in(page)
