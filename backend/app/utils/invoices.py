"""Store invoice PDF rendering and storage.

Invoice HTML is captured by the browser extension from the store's order
details page and converted to a PDF here when an import is applied.

Rendering uses headless Chromium (Playwright): `page.pdf()` runs the same
print pipeline as Chrome's print preview and applies the page's @media print
styles, so the PDF matches what the store's "Print Invoice" button shows —
regardless of which browser captured the HTML.

To keep the apply click fast, PDFs are pre-rendered in the background as soon
as the import review page fetches its bulk session (see prewarm_invoice_pdf).
Pre-rendered files are keyed by a hash of the invoice HTML and stored under
`{INVOICE_DIR}/prerender/`; apply waits for the finished file and moves it to
its final `{order_id}.pdf` name, falling back to a synchronous render.
"""
import hashlib
import logging
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Cap how long Chromium may spend loading remote resources (product images).
_RENDER_TIMEOUT_MS = 30_000
# How long apply waits for a background render that may still be queued behind
# other prerender jobs before falling back to rendering synchronously.
_PRERENDER_WAIT_SECONDS = 90
# Prerendered PDFs whose order was never applied are cleaned up after this age.
_PRERENDER_MAX_AGE_SECONDS = 24 * 3600

# Chromium is heavy; render at most two invoices concurrently.
_prerender_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="invoice-prerender")
_prerender_jobs: dict[str, Future] = {}
_prerender_lock = threading.Lock()


def invoice_dir() -> Path:
    path = Path(settings.invoice_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def invoice_pdf_path(filename: str) -> Path:
    return invoice_dir() / filename


def _prerender_dir() -> Path:
    path = invoice_dir() / "prerender"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _invoice_html_key(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _render_html_to_pdf(html: str, target: Path) -> None:
    """Render HTML to a PDF file with headless Chromium. Raises on failure."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            try:
                page.set_content(html, wait_until="networkidle", timeout=_RENDER_TIMEOUT_MS)
            except Exception:
                # Slow/blocked images should not sink the render; print
                # whatever has loaded so far.
                logger.info("Invoice page did not reach network idle; rendering anyway")
            try:
                # networkidle can fire before every image has decoded;
                # explicitly wait until all images are done loading.
                page.wait_for_function(
                    "() => Array.from(document.images).every((img) => img.complete)",
                    timeout=_RENDER_TIMEOUT_MS,
                )
            except Exception:
                logger.info("Not all invoice images loaded; rendering anyway")
            pdf_bytes = page.pdf(format="Letter", print_background=True)
        finally:
            browser.close()

    target.write_bytes(pdf_bytes)


def _cleanup_stale_prerenders() -> None:
    """Best-effort removal of prerendered PDFs that were never applied."""
    try:
        cutoff = time.time() - _PRERENDER_MAX_AGE_SECONDS
        for path in _prerender_dir().glob("*.pdf"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    except Exception:
        # never let cleanup interfere with rendering
        pass


def prewarm_invoice_pdf(html: str) -> None:
    """Start rendering an invoice PDF in the background.

    Called when the import review page loads its payloads, so the PDF is
    (usually) already finished by the time the user clicks apply. Safe to call
    repeatedly with the same HTML; only one render per distinct capture runs.
    """
    if not html:
        return
    key = _invoice_html_key(html)
    with _prerender_lock:
        if key in _prerender_jobs:
            return
        target = _prerender_dir() / f"{key}.pdf"
        if target.is_file():
            # Already rendered by a previous session with identical capture.
            return
        future = _prerender_executor.submit(_render_html_to_pdf, html, target)
        _prerender_jobs[key] = future
    _cleanup_stale_prerenders()


def render_invoice_pdf(html: str, order_id: int) -> str | None:
    """Produce `{order_id}.pdf` in the invoice dir for the given invoice HTML.

    Prefers a pre-rendered PDF (waiting for an in-flight background render if
    needed) and falls back to rendering synchronously. Returns the stored
    filename, or None if rendering failed (never raises so a bad invoice
    cannot fail the order import).
    """
    filename = f"{order_id}.pdf"
    target = invoice_pdf_path(filename)
    key = _invoice_html_key(html)

    with _prerender_lock:
        future = _prerender_jobs.get(key)

    if future is not None:
        try:
            future.result(timeout=_PRERENDER_WAIT_SECONDS)
        except Exception:
            logger.info(
                "Background invoice render was not usable for order %s; rendering synchronously",
                order_id,
            )
        finally:
            with _prerender_lock:
                _prerender_jobs.pop(key, None)

    prerendered = _prerender_dir() / f"{key}.pdf"
    if prerendered.is_file():
        try:
            shutil.move(str(prerendered), str(target))
            return filename
        except OSError:
            logger.info(
                "Could not move prerendered invoice for order %s; rendering synchronously",
                order_id,
            )

    try:
        _render_html_to_pdf(html, target)
        return filename
    except Exception:
        logger.warning("Could not render invoice PDF for order %s", order_id, exc_info=True)
        return None
