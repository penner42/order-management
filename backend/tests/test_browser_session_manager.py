"""Browser session capacity and login verification regressions."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser_automation.session_manager import (
    COSTCO_HOME_URL,
    COSTCO_MYACCOUNT_URL,
    CostcoSessionWarmupError,
    LiveSession,
    SessionManager,
    establish_costco_session,
    looks_like_costco_signin,
)


class CostcoSessionWarmupTests(unittest.IsolatedAsyncioTestCase):
    def page(self, account_url, *, account_status=200):
        page = Mock(url="about:blank", wait_for_timeout=AsyncMock())

        async def goto(url, **kwargs):
            if url == COSTCO_HOME_URL:
                page.url = url
                return Mock(status=200)
            self.assertEqual(url, COSTCO_MYACCOUNT_URL)
            page.url = account_url
            return Mock(status=account_status)

        page.goto = AsyncMock(side_effect=goto)
        return page

    async def test_distinguishes_the_account_app_from_sign_in(self):
        self.assertTrue(await establish_costco_session(
            self.page(f"{COSTCO_MYACCOUNT_URL}#/app/sample/ordersandpurchases"), timeout_s=0,
        ))
        self.assertFalse(await establish_costco_session(
            self.page("https://signin.costco.com/LogonForm"), timeout_s=0,
        ))

    async def test_waits_for_sign_in_to_redirect_to_the_account_app(self):
        page = self.page("https://signin.costco.com/LogonForm")
        goto = page.goto.side_effect

        async def goto_then_redirect(url, **kwargs):
            response = await goto(url, **kwargs)
            if url == COSTCO_MYACCOUNT_URL:
                asyncio.get_running_loop().call_later(
                    1, setattr, page, "url", f"{COSTCO_MYACCOUNT_URL}#/app/sample/ordersandpurchases",
                )
            return response

        page.goto.side_effect = goto_then_redirect
        self.assertTrue(await establish_costco_session(page, timeout_s=3))

    async def test_reports_stalled_account_and_sign_in_redirects(self):
        for url, message in (
            (COSTCO_MYACCOUNT_URL, "account app did not finish loading"),
            ("https://signin.costco.com/oauth2/v2.0/authorize", "sign-in redirect did not complete"),
            ("https://www.costco.com/OAuthLogonCmd?from=signin.costco.com", "sign-in redirect did not complete"),
            ("https://www.costco.com/blocked", "showed a block page"),
        ):
            with self.subTest(url=url), self.assertRaisesRegex(CostcoSessionWarmupError, message):
                await establish_costco_session(self.page(url), timeout_s=0)

    async def test_reports_http_and_navigation_failures_without_callback_data(self):
        page = self.page(COSTCO_MYACCOUNT_URL, account_status=403)
        with self.assertRaisesRegex(CostcoSessionWarmupError, "HTTP 403 while opening the account page"):
            await establish_costco_session(page, timeout_s=0)

        page.goto.side_effect = PlaywrightTimeoutError("https://www.costco.com/OAuthLogonCmd?code=private")
        with self.assertRaisesRegex(CostcoSessionWarmupError, "^Timed out loading the Costco homepage during session warm-up\\.$"):
            await establish_costco_session(page, timeout_s=0)

    def test_signin_markers_in_the_query_do_not_match(self):
        self.assertFalse(looks_like_costco_signin(f"{COSTCO_MYACCOUNT_URL}?next=https://signin.costco.com/LogonForm"))


class _FakePage:
    url = "about:blank"
    viewport_size = {"width": 1024, "height": 720}

    async def bring_to_front(self):
        pass


class _FakeCDP:
    def __init__(self):
        self.commands = []

    async def send(self, method, params):
        self.commands.append((method, params))


class _FakeContext:
    def __init__(self) -> None:
        self.pages = [_FakePage()]
        self.closed = False

    async def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    async def new_cdp_session(self, page):
        return _FakeCDP()

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
    async def test_chromium_initial_page_and_popup_disable_native_passkey_ui(self):
        manager = _FakeSessionManager(capacity=1)
        session = await manager.ensure_session(1, mode="login", retailer="amazon")
        initial_cdp = session.passkey_ui_sessions[session.page]
        self.assertEqual(initial_cdp.commands, [("WebAuthn.enable", {"enableUI": False})])
        await manager._configure_passkey_ui(session, session.page)
        self.assertIs(session.passkey_ui_sessions[session.page], initial_cdp)
        popup = await session.context.new_page()
        await manager._adopt_page(session, popup)
        self.assertIs(session.page, popup)
        self.assertEqual(session.passkey_ui_sessions[popup].commands,
                         [("WebAuthn.enable", {"enableUI": False})])
        await manager.close_session(1)

    async def test_firefox_does_not_use_chromium_passkey_configuration(self):
        manager = _FakeSessionManager(capacity=1)
        session = await manager.ensure_session(1, mode="login", retailer="walmart")
        self.assertEqual(session.passkey_ui_sessions, {})
        await manager.close_session(1)

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
