"""Concurrency regressions for browser session capacity management."""
from __future__ import annotations

import asyncio
import unittest

from app.browser_automation.session_manager import LiveSession, SessionManager


class _FakePage:
    url = "about:blank"
    viewport_size = {"width": 1024, "height": 720}


class _FakeContext:
    def __init__(self) -> None:
        self.pages = [_FakePage()]
        self.closed = False

    async def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    def on(self, _event: str, _handler) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _FakeSessionManager(SessionManager):
    def __init__(self, capacity: int) -> None:
        super().__init__()
        self._slot = asyncio.Semaphore(capacity)
        self.launch_count = 0

    async def _launch_camoufox_context(self, *args, **kwargs):
        self.launch_count += 1
        return _FakeContext(), None

    async def _launch_chromium_context(self, *args, **kwargs):
        self.launch_count += 1
        return _FakeContext(), None


class BrowserSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_open_does_not_block_close_that_releases_capacity(self) -> None:
        manager = _FakeSessionManager(capacity=1)
        existing_context = _FakeContext()
        manager._sessions[1] = LiveSession(
            profile_id=1,
            context=existing_context,
            page=existing_context.pages[0],
        )
        await manager.acquire_slot()

        opening = asyncio.create_task(
            manager.ensure_session(2, mode="import", retailer="amazon")
        )
        await asyncio.sleep(0)
        self.assertFalse(opening.done())

        closing = asyncio.create_task(manager.close_session(1))
        opened, _ = await asyncio.wait_for(
            asyncio.gather(opening, closing),
            timeout=1,
        )

        self.assertTrue(existing_context.closed)
        self.assertIs(manager.get(2), opened)
        self.assertEqual(manager.launch_count, 1)
        await manager.close_session(2)

    async def test_concurrent_requests_for_one_profile_launch_once(self) -> None:
        manager = _FakeSessionManager(capacity=2)

        # Hold all capacity so both callers complete the initial lookup and
        # wait before either can register the profile.
        await manager.acquire_slot()
        await manager.acquire_slot()
        first_task = asyncio.create_task(
            manager.ensure_session(1, mode="import", retailer="amazon")
        )
        second_task = asyncio.create_task(
            manager.ensure_session(1, mode="import", retailer="amazon")
        )
        await asyncio.sleep(0)
        manager.release_slot()
        manager.release_slot()

        first, second = await asyncio.gather(
            first_task,
            second_task,
        )

        self.assertIs(first, second)
        self.assertEqual(manager.launch_count, 1)
        await manager.close_session(1)

        # The duplicate request returned its permit, so all configured capacity
        # is available after the sole live session closes.
        await asyncio.wait_for(manager.acquire_slot(), timeout=1)
        await asyncio.wait_for(manager.acquire_slot(), timeout=1)
        manager.release_slot()
        manager.release_slot()


if __name__ == "__main__":
    unittest.main()
