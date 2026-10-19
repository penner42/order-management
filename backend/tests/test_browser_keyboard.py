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
        # These are the down/char/up messages emitted by the live-view canvas.
        await self.manager.dispatch_input(self.session, {"type": "key", "event": "down", "key": key})
        if len(key) == 1:
            await self.manager.dispatch_input(self.session, {"type": "key", "event": "char", "key": key, "text": key})
        await self.manager.dispatch_input(self.session, {"type": "key", "event": "up", "key": key})

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


if __name__ == "__main__":
    unittest.main()
