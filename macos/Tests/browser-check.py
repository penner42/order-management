"""Real ARM browser smoke checks against inline fixtures; no store requests."""
import platform
from playwright.sync_api import sync_playwright
from camoufox.sync_api import Camoufox

assert platform.machine() in ("aarch64", "arm64"), platform.machine()
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page()
    page.set_content("<title>ARM invoice</title><h1>Invoice ARM-001</h1><p>Total: $12.34</p>")
    assert page.title() == "ARM invoice"
    pdf = page.pdf()
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1000
    browser.close()
with Camoufox(headless=True) as browser:
    page = browser.new_page()
    page.set_content("<title>ARM automation</title><button id='test'>Continue</button>")
    assert page.title() == "ARM automation"
    page.locator("#test").click()
    assert page.screenshot().startswith(b"\x89PNG")
print("PASS native ARM Chromium/PDF and Camoufox fixture automation")
