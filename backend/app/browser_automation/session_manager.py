"""Manage Playwright persistent contexts per browser profile."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.browser_automation.paths import profile_user_data_dir
from app.config import settings

logger = logging.getLogger(__name__)

AMAZON_ORDERS_URL = "https://www.amazon.com/your-orders/orders?disableCsd=missing-library"
AMAZON_SIGNIN_HINTS = ("/ap/signin", "/ap/mfa", "/ap/cvf")


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

    async def ensure_session(self, profile_id: int, *, mode: str, start_url: str | None = None) -> LiveSession:
        async with self._global_lock:
            existing = self._sessions.get(profile_id)
            if existing:
                existing.mode = mode
                if start_url:
                    try:
                        await existing.page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
                    except Exception as exc:
                        logger.warning("Navigate existing session failed: %s", exc)
                return existing

            await self.acquire_slot()
            try:
                user_data = str(profile_user_data_dir(profile_id))
                pw = await async_playwright().start()
                context = await pw.chromium.launch_persistent_context(
                    user_data,
                    headless=True,
                    viewport={"width": 1280, "height": 800},
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                session = LiveSession(
                    profile_id=profile_id,
                    playwright=pw,
                    context=context,
                    page=page,
                    mode=mode,
                )
                self._sessions[profile_id] = session
                if start_url:
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
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
                    "maxWidth": 1280,
                    "maxHeight": 800,
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
