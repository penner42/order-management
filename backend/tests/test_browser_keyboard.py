"""Live-view keyboard regressions in disposable Chromium pages, without logins."""
import unittest

from playwright.async_api import async_playwright

from app.browser_automation.session_manager import LiveSession, SessionManager


class BrowserKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.session = LiveSession(profile_id=0, context=self.context, page=self.page,
                                   browser="chromium", retailer="monarch")
        self.manager = SessionManager()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def key(self, key):
        event = "char" if len(key) == 1 else "press"
        await self.manager.dispatch_input(self.session, {"type": "key", "event": event, "key": key})

    async def test_digits_advance_through_code_cells_and_navigation_still_works(self):
        await self.page.set_content("""
          <div role="group" aria-label="Verification code">
            <input inputmode="numeric" autocomplete="one-time-code" maxlength="6">
            <input inputmode="numeric" autocomplete="off">
            <input inputmode="numeric" autocomplete="off">
            <input inputmode="numeric" autocomplete="off">
            <input inputmode="numeric" autocomplete="off">
            <input inputmode="numeric" autocomplete="off">
          </div>
          <button>Continue</button>
          <script>
            const cells = [...document.querySelectorAll('input')];
            window.code = Array(6).fill('');
            window.printableKeydowns = 0;
            cells.forEach((cell, index) => {
              cell.onfocus = () => cell.select();
              cell.onkeydown = event => {
                // Reproduce a widget that rejects replayed printable key events
                // but accepts the input events used by working paste insertion.
                if (/^[0-9]$/.test(event.key)) {
                  window.printableKeydowns++;
                  event.preventDefault();
                } else if (event.key === 'Backspace') {
                  event.preventDefault();
                  const target = cell.value ? index : Math.max(0, index - 1);
                  window.code[target] = cells[target].value = '';
                  cells[target].focus();
                } else if (event.key === 'ArrowLeft') {
                  event.preventDefault(); cells[Math.max(0, index - 1)].focus();
                } else if (event.key === 'ArrowRight') {
                  event.preventDefault(); cells[Math.min(5, index + 1)].focus();
                }
              };
              cell.oninput = event => {
                if (!event.data || !/^[0-9]+$/.test(event.data)) return;
                [...event.data].slice(0, 6 - index).forEach((digit, offset) => {
                  window.code[index + offset] = cells[index + offset].value = digit;
                });
                cells[Math.min(5, index + event.data.length)].focus();
              };
            });
          </script>
        """)
        await self.page.locator('input').first.focus()
        for digit in "123456":
            await self.key(digit)
        self.assertEqual(await self.page.evaluate("window.code.join('')"), "123456")
        self.assertEqual(await self.page.evaluate("window.printableKeydowns"), 0)
        await self.key("Backspace")
        await self.key("ArrowLeft")
        await self.key("9")
        await self.key("8")
        self.assertEqual(await self.page.evaluate("window.code.join('')"), "123498")
        await self.key("Tab")
        self.assertEqual(await self.page.evaluate("document.activeElement.tagName"), "BUTTON")
        # Pasting a complete code remains supported.
        await self.page.locator('input').first.focus()
        await self.manager.dispatch_input(self.session, {"type": "paste", "text": "654321"})
        self.assertEqual(await self.page.evaluate("window.code.join('')"), "654321")

    async def test_ordinary_fields_keep_keyboard_events_and_shortcuts(self):
        await self.page.set_content("""<input autocomplete="username"><input type="password">
          <script>window.keys=[];document.addEventListener('keydown', e=>window.keys.push(e.key));</script>""")
        await self.page.locator('input').first.focus()
        for char in "a1":
            await self.key(char)
        self.assertEqual(await self.page.locator('input').first.input_value(), "a1")
        self.assertEqual(await self.page.evaluate("window.keys"), ["a", "1"])
        await self.key("Tab")
        await self.key("X")
        self.assertEqual(await self.page.locator('input[type=password]').input_value(), "X")

        await self.manager.dispatch_input(
            self.session, {"type": "key", "event": "press", "key": "Tab", "modifiers": 8}
        )
        await self.manager.dispatch_input(
            self.session, {"type": "key", "event": "press", "key": "KeyA", "modifiers": 2}
        )
        await self.manager.dispatch_input(
            self.session, {"type": "key", "event": "press", "key": "AudioVolumeUp", "modifiers": 2}
        )
        await self.key("z")
        self.assertEqual(await self.page.locator('input').first.input_value(), "z")
        await self.manager.dispatch_input(self.session, {"type": "paste", "text": "@example.test"})
        self.assertEqual(await self.page.locator('input').first.input_value(), "z@example.test")

    async def test_password_input_and_submit_work_during_a_passkey_request(self):
        self.session.retailer = "amazon"
        await self.manager._configure_passkey_ui(self.session, self.page)
        await self.context.route("https://passkey.test/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body='''
              <form><input type="password" required><button>Sign in</button></form>
              <script>
                window.submissions=0;
                document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submissions++};
                window.authAbort=new AbortController();
                navigator.credentials.get({publicKey: {
                  challenge: new Uint8Array(32), rpId: 'passkey.test',
                  allowCredentials: [{type: 'public-key', id: new Uint8Array(32), transports: ['internal']}],
                  userVerification: 'required'
                }, signal: window.authAbort.signal}).catch(()=>{});
              </script>
            '''))
        await self.page.goto("https://passkey.test/")
        box = await self.page.locator('input').bounding_box()
        for event in ("down", "up"):
            await self.manager.dispatch_input(self.session, {"type": "mouse", "event": event,
                "x": box['x'] + box['width'] / 2, "y": box['y'] + box['height'] / 2})
        await self.manager.dispatch_input(self.session, {"type": "paste", "text": "dummy-password"})
        self.assertEqual(await self.page.locator('input').input_value(), "dummy-password")
        await self.key("Enter")
        self.assertEqual(await self.page.evaluate("window.submissions"), 1)
        await self.page.evaluate("window.authAbort.abort()")


if __name__ == "__main__":
    unittest.main()
