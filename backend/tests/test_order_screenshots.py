"""Screenshot history tests use only temporary files and an isolated SQLite DB."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading

from fastapi import HTTPException
from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.config import settings
from app.models import BrowserProfile, Order, OrderScreenshot, Store, StoreAccount
from app.schemas.store_import import StoreOrderImportPayload
from app.utils.order_screenshots import (
    attach_screenshot, cleanup_screenshots, fingerprint, semantic_snapshot,
)
from app.browser_automation.screenshots import capture_order_screenshot
from app.routers.orders import delete_order, get_order_screenshot, list_order_screenshots


def order_payload(retailer="amazon"):
    return {
        "store": retailer, "externalOrder": {"id": "123-456", "orderDate": "2026-11-17", "url": "https://example.test/"},
        "items": [{"name": "Example purchase", "quantities": {"ordered": 2},
                   "pricing": {"unitPrice": 20}, "upc": "1234", "imageUrl": "a.png",
                   "shipments": [{"shipmentId": "generated-1", "quantity": 2}]}],
        "shipments": [{"shipmentId": "generated-1", "trackingNumber": "TRACK123", "status": {"normalizedStatus": "shipped"}}],
        "totals": {"grandTotal": 40},
    }


class ScreenshotStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = patch("app.utils.order_screenshots.screenshot_dir", return_value=self.root)
        self.storage.start()
        self.api_storage = patch("app.routers.orders.screenshot_dir", return_value=self.root)
        self.api_storage.start()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(Store(id=1, name="Test store"))
        self.order = Order(store_id=1, store_order_number="123-456")
        self.db.add(self.order)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.storage.stop()
        self.api_storage.stop()
        self.temp.cleanup()

    def stage(self, payload, **overrides):
        token = uuid.uuid4().hex
        stage = self.root / "staging"
        stage.mkdir(exist_ok=True)
        snapshot = semantic_snapshot(payload)
        metadata = dict(number="123-456", retailer=payload["store"], store_id=1, store_account_id=None,
                        snapshot=snapshot, fingerprint=fingerprint(snapshot), captured_at=datetime.now(timezone.utc).isoformat())
        metadata.update(overrides)
        (stage / f"{token}.json").write_text(json.dumps(metadata))
        (stage / f"{token}.png").write_bytes(b"image")
        return StoreOrderImportPayload.model_validate({**payload, "screenshotToken": token})

    def save(self, payload):
        attach_screenshot(self.db, self.order, self.stage(payload))
        self.db.commit()

    def test_first_unchanged_changed_and_reverted(self):
        payload = order_payload()
        self.save(payload)
        self.save(payload)  # Duplicate stages from overlapping capture jobs.
        self.assertEqual(self.db.query(OrderScreenshot).count(), 1)
        changed = copy.deepcopy(payload)
        changed["totals"]["grandTotal"] = 39
        self.save(changed)
        self.save(payload)  # A -> B -> A must retain all three observations.
        rows = self.db.query(OrderScreenshot).order_by(OrderScreenshot.id).all()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.summary for r in rows], ["Initial capture", "Totals", "Totals"])
        self.assertTrue(all((self.root / r.filename).is_file() for r in rows))

    def test_semantic_comparison_ignores_visual_metadata_and_reordering(self):
        payload = order_payload()
        changed = copy.deepcopy(payload)
        changed["capturedAt"] = "later"
        changed["externalOrder"]["url"] = "different"
        changed["invoiceHtml"] = "different"
        changed["items"][0].update(upc="5678", imageUrl="b.png", productUrl="random")
        changed["shipments"][0]["shipmentId"] = "generated-2"
        changed["items"][0]["shipments"][0]["shipmentId"] = "generated-2"
        changed["totals"]["grandTotal"] = 40.0
        self.assertEqual(semantic_snapshot(payload), semantic_snapshot(changed))
        for section, value in [("totals", {"grandTotal": 41}), ("shippingAddress", {"postalCode": "12345"}),
                               ("paymentMethods", [{"last4": "4321"}]),
                               ("shipments", [{"deliveryDate": "2026-11-20"}])]:
            modified = {**payload, section: value}
            self.assertNotEqual(semantic_snapshot(payload), semantic_snapshot(modified))
        payload["items"].append({"name": "Second item"})
        changed = copy.deepcopy(payload)
        changed["items"].reverse()
        self.assertEqual(semantic_snapshot(payload), semantic_snapshot(changed))

    def test_invalid_stage_cannot_cross_accounts_orders_or_payloads(self):
        for overrides in [{"number": "other-order"}, {"store_account_id": 9}, {"store_id": 9}, {"retailer": "walmart"}]:
            attach_screenshot(self.db, self.order, self.stage(order_payload(), **overrides))
        payload = self.stage(order_payload())
        payload.totals = {"grandTotal": 1}
        attach_screenshot(self.db, self.order, payload)
        self.db.commit()
        self.assertEqual(self.db.query(OrderScreenshot).count(), 0)

    def test_replayed_stage_and_late_review_do_not_add_versions(self):
        first = self.stage(order_payload())
        late = self.stage({**order_payload(), "totals": {"grandTotal": 12}},
                          captured_at="2000-01-01T00:00:00+00:00")
        attach_screenshot(self.db, self.order, first)
        self.db.commit()
        self.save({**order_payload(), "totals": {"grandTotal": 39}})
        attach_screenshot(self.db, self.order, first)
        attach_screenshot(self.db, self.order, late)
        self.db.commit()
        self.assertEqual(self.db.query(OrderScreenshot).count(), 2)

    def test_rollback_can_retry_the_same_stage(self):
        payload = self.stage(order_payload())
        attach_screenshot(self.db, self.order, payload)
        self.db.flush()
        self.db.rollback()
        self.assertEqual(self.db.query(OrderScreenshot).count(), 0)
        attach_screenshot(self.db, self.order, payload)
        self.db.commit()
        self.assertEqual(self.db.query(OrderScreenshot).count(), 1)

    def test_failed_capture_does_not_advance_baseline_and_missing_image_is_repaired(self):
        self.save(order_payload())
        row = self.db.query(OrderScreenshot).one()
        (self.root / row.filename).unlink()
        self.save(order_payload())
        self.assertEqual(self.db.query(OrderScreenshot).count(), 1)
        missing = StoreOrderImportPayload.model_validate({**order_payload(), "screenshotToken": "a" * 32})
        attach_screenshot(self.db, self.order, missing)
        self.db.commit()
        self.assertEqual(self.db.query(OrderScreenshot).count(), 1)

    def test_framing_refresh_replaces_latest_image_without_an_order_change(self):
        payload = order_payload()
        self.save(payload)
        row = self.db.query(OrderScreenshot).one()
        original_id, original_filename = row.id, row.filename
        staged = self.stage(payload, layout_version=2)
        attach_screenshot(self.db, self.order, staged)
        self.db.commit()
        row = self.db.query(OrderScreenshot).one()
        self.assertEqual(row.id, original_id)
        self.assertEqual(row.summary, "Initial capture")
        self.assertEqual(row.layout_version, 2)
        self.assertNotEqual(row.filename, original_filename)
        self.assertTrue((self.root / row.filename).is_file())
        attach_screenshot(self.db, self.order, self.stage(payload, layout_version=1))
        self.db.commit()
        self.assertEqual(self.db.query(OrderScreenshot).one().filename, row.filename)

    def test_endpoints_scope_images_to_the_order(self):
        self.save(order_payload())
        row = self.db.query(OrderScreenshot).one()
        versions = list_order_screenshots(self.order.id, self.db, None)
        self.assertEqual(versions[0]["id"], row.id)
        self.assertEqual(get_order_screenshot(self.order.id, row.id, self.db, None).media_type, "image/png")
        with self.assertRaises(HTTPException) as error:
            get_order_screenshot(self.order.id + 1, row.id, self.db, None)
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_order_removes_history_and_images(self):
        self.save(order_payload())
        image = self.root / self.db.query(OrderScreenshot).one().filename
        delete_order(self.order.id, self.db, None)
        self.assertEqual(self.db.query(OrderScreenshot).count(), 0)
        self.assertFalse(image.exists())

    def test_orphan_collection_retains_referenced_and_recent_files(self):
        import os
        self.save(order_payload())
        row = self.db.query(OrderScreenshot).one()
        old = self.root / "orphan.png"
        recent = self.root / "recent.png"
        old.write_bytes(b"orphan")
        recent.write_bytes(b"recent")
        os.utime(old, (0, 0))
        os.utime(self.root / row.filename, (0, 0))
        with patch("app.utils.order_screenshots._last_cleanup", 0):
            cleanup_screenshots(self.db)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue((self.root / row.filename).exists())


@unittest.skipUnless(settings.database_url.startswith('postgresql'), 'requires Docker PostgreSQL')
class ScreenshotConcurrencyTests(unittest.TestCase):
    def test_concurrent_jobs_serialize_history(self):
        from sqlalchemy import text
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_screenshot_" + uuid.uuid4().hex
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        engine = create_engine(admin.url.set(database=name))
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                db.add(Store(id=1, name="Isolated screenshot test"))
                db.flush()
                db.add(Order(id=1, store_id=1, store_order_number="123-456"))
                db.commit()
            with tempfile.TemporaryDirectory() as directory, patch(
                "app.utils.order_screenshots.screenshot_dir", return_value=Path(directory)
            ):
                root = Path(directory)
                stage = root / "staging"
                stage.mkdir()
                payloads = []
                for _ in range(2):
                    token = uuid.uuid4().hex
                    snapshot = semantic_snapshot(order_payload())
                    (stage / f"{token}.json").write_text(json.dumps(dict(
                        number="123-456", retailer="amazon", store_id=1, store_account_id=None,
                        snapshot=snapshot, fingerprint=fingerprint(snapshot),
                        captured_at=datetime.now(timezone.utc).isoformat(),
                    )))
                    (stage / f"{token}.png").write_bytes(b"image")
                    payloads.append(StoreOrderImportPayload.model_validate({**order_payload(), "screenshotToken": token}))
                locked, release, second_started = threading.Event(), threading.Event(), threading.Event()

                def writer(index):
                    with Session(engine) as db:
                        if index == 0:
                            order = db.query(Order).filter_by(id=1).with_for_update().one()
                            attach_screenshot(db, order, payloads[index])
                            db.flush()
                            locked.set()
                            if not release.wait(10):
                                raise AssertionError("Test did not release first writer")
                        else:
                            second_started.set()
                            attach_screenshot(db, db.get(Order, 1), payloads[index])
                        db.commit()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(writer, 0)
                    self.assertTrue(locked.wait(10))
                    second = pool.submit(writer, 1)
                    try:
                        self.assertTrue(second_started.wait(10))
                        with self.assertRaises(FutureTimeout):
                            second.result(timeout=0.2)
                    finally:
                        release.set()
                    first.result(timeout=10)
                    second.result(timeout=10)
                with Session(engine) as db:
                    self.assertEqual(db.query(OrderScreenshot).count(), 1)
        finally:
            engine.dispose()
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()


class ScreenshotBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add(Store(id=1, name="Test store"))
            db.add(BrowserProfile(id=1, store_id=1, retailer="amazon"))
            db.add(Order(id=1, store_id=1, store_order_number="123-456"))
            db.commit()
        self.patches = [
            patch("app.browser_automation.screenshots.SessionLocal", lambda: Session(self.engine)),
            patch("app.browser_automation.screenshots.screenshot_dir", return_value=self.root),
            patch("app.utils.order_screenshots.screenshot_dir", return_value=self.root),
        ]
        for item in self.patches:
            item.start()
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        for item in self.patches:
            item.stop()
        self.engine.dispose()
        self.temp.cleanup()

    async def show_order(self, retailer, number="123-456"):
        routes = {"amazon": f"https://example.test/order-details?orderID={number}",
                  "walmart": f"https://example.test/orders/{number}",
                  "costco": f"https://example.test/#/app/orderdetails/{number}"}
        await self.page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
        await self.page.goto(routes[retailer])
        await self.page.set_content('''<style>body {margin:0} main {width:800px} article {height:1200px}</style>
            <nav>Navigation outside crop</nav><main><h1>Order 123-456</h1>
            <article>Example purchase · quantity 2 · total $40 · tracking TRACK123</article>
            <aside data-testid="recommendations">Recommendations</aside><footer>Footer inside main</footer></main>
            <footer>Page footer</footer>''')
        with Session(self.engine) as db:
            db.get(BrowserProfile, 1).retailer = retailer
            db.commit()

    async def test_cropped_real_pages_for_all_retailers_and_long_orders(self):
        for retailer in ("amazon", "walmart", "costco"):
            await self.show_order(retailer)
            payload = order_payload(retailer)
            await capture_order_screenshot(self.page, payload, 1)
            self.assertIn("screenshotToken", payload)
            path = self.root / "staging" / f'{payload["screenshotToken"]}.png'
            # PNG's IHDR dimensions verify the crop, including tall purchase
            # content but excluding both recommendations and footers.
            data = path.read_bytes()
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            self.assertEqual(width, 848)
            self.assertGreater(height, 1200)
            self.assertLess(height, 1350)
            self.assertEqual(await self.page.locator('[data-testid="recommendations"]').evaluate(
                "element => getComputedStyle(element).display"), "block")
            self.assertEqual(await self.page.locator('[data-order-screenshot]').count(), 0)

    async def test_unchanged_order_skips_screenshot_and_other_account_does_not(self):
        await self.show_order("amazon")
        payload = order_payload()
        await capture_order_screenshot(self.page, payload, 1)
        with Session(self.engine) as db:
            attach_screenshot(db, db.get(Order, 1), StoreOrderImportPayload.model_validate(payload))
            db.commit()
        with patch.object(self.page, "wait_for_function", side_effect=AssertionError("Must skip before crop")):
            unchanged = order_payload()
            await capture_order_screenshot(self.page, unchanged, 1)
            self.assertNotIn("screenshotToken", unchanged)
        with Session(self.engine) as db:
            db.add(StoreAccount(id=2, store_id=1, name="Other account"))
            db.add(BrowserProfile(id=2, store_id=1, store_account_id=2, retailer="amazon"))
            db.commit()
        other = order_payload()
        await capture_order_screenshot(self.page, other, 2)
        self.assertIn("screenshotToken", other)

    async def test_wrong_detail_route_and_missing_crop_do_not_capture(self):
        await self.show_order("costco", "different-order")
        payload = order_payload("costco")
        await capture_order_screenshot(self.page, payload, 1)
        self.assertNotIn("screenshotToken", payload)

    async def test_crop_includes_summary_siblings_and_waits_for_hydration(self):
        await self.show_order("amazon")
        await self.page.set_content('''<main style="width:800px"><div id="orderDetails">
            <h1>Order 123-456</h1><p>Example purchase</p></div>
            <section id="summary">Shipping summary</section>
            <aside aria-label="Recommendations">Unrelated purchase</aside></main>
            <div id="floating" style="position:fixed;top:0;left:0;width:800px;height:500px;background:red">Chat</div>
            <script>setTimeout(() => document.querySelector('#summary').innerHTML =
              '123 Test Street, 12345. Visa ending in 4321', 100)</script>''')
        payload = order_payload()
        payload["shippingAddress"] = {"addressLine1": "123 Test Street", "postalCode": "12345"}
        payload["paymentMethods"] = [{"last4": "4321"}]
        await capture_order_screenshot(self.page, payload, 1)
        self.assertIn("screenshotToken", payload)
        self.assertEqual(await self.page.locator('#floating').evaluate(
            "element => getComputedStyle(element).visibility"), "visible")
        self.assertEqual(await self.page.locator('[data-order-screenshot-overlay]').count(), 0)

    async def test_missing_crop_does_not_capture(self):
        await self.show_order("amazon")
        await self.page.set_content('<main>Loading…</main>')
        # Exercise real root validation with a short test-only wait.
        original = self.page.wait_for_function
        async def short_wait(*args, **kwargs):
            kwargs["timeout"] = 100
            return await original(*args, **kwargs)
        with patch.object(self.page, "wait_for_function", side_effect=short_wait):
            payload = order_payload()
            await capture_order_screenshot(self.page, payload, 1)
        self.assertNotIn("screenshotToken", payload)

    async def test_costco_react_order_panel_outside_storefront_main(self):
        await self.show_order("costco")
        await self.page.set_content('''<main id="tiles-body-attribute">Storefront navigation</main>
            <div id="account-app"><nav>Account sidebar</nav><div class="MuiBox-root">
            <div><h1>Order Details</h1></div><p>Order 123-456</p>
            <article style="height:100px">Example purchase, quantity 2, total $40</article>
            <section>123 Test Street, 12345. Visa ending in 4321</section>
            </div><aside aria-label="Recommendations">Unrelated products</aside></div>''')
        payload = order_payload("costco")
        payload["shippingAddress"] = {"addressLine1": "123 Test Street", "postalCode": "12345"}
        payload["paymentMethods"] = [{"last4": "4321"}]
        await capture_order_screenshot(self.page, payload, 1)
        self.assertIn("screenshotToken", payload)
        metadata = json.loads((self.root / "staging" / f'{payload["screenshotToken"]}.json').read_text())
        self.assertEqual(metadata["snapshot"], semantic_snapshot(payload))

    async def test_costco_expands_order_summary_before_cropping(self):
        await self.show_order("costco")
        await self.page.set_content('''<main id="tiles-body-attribute" style="width:1000px">
            <nav style="width:300px">Account navigation</nav><div style="width:650px">
            <div><h1>Order Details</h1><p>Order 123-456</p></div>
            <div role="button" aria-expanded="false" onclick="this.setAttribute('aria-expanded','true');
              document.getElementById('summary').hidden=false">
              <h2>Order Summary</h2><button>Show Details</button></div>
            <section id="summary" hidden>123 Test Street, 12345. Visa ending in 4321</section>
            <article style="height:200px">Example purchase, quantity 2, total $40</article>
            </div></main>''')
        payload = order_payload("costco")
        payload["shippingAddress"] = {"addressLine1": "123 Test Street", "postalCode": "12345"}
        payload["paymentMethods"] = [{"last4": "4321"}, {"last4": "9876"}, {"last4": "9876"}]
        payload["items"].append({"name": "Bundled protection plan present only in API"})
        await capture_order_screenshot(self.page, payload, 1)
        self.assertIn("screenshotToken", payload)
        self.assertTrue(await self.page.locator('#summary').is_visible())
        image = (self.root / "staging" / f'{payload["screenshotToken"]}.png').read_bytes()
        self.assertEqual(int.from_bytes(image[16:20], "big"), 698)

    async def test_padding_covers_overflow_and_restores_viewport_for_all_retailers(self):
        for retailer in ("amazon", "walmart", "costco"):
            await self.show_order(retailer)
            original_viewport = self.page.viewport_size
            await self.page.locator('main').evaluate("""element => {
              element.style.position = 'relative';
              const label = document.createElement('span');
              label.textContent = 'Print invoice';
              label.style.cssText = 'position:absolute;right:-36px;top:30px;width:100px;height:20px';
              element.appendChild(label);
            }""")
            payload = order_payload(retailer)
            await capture_order_screenshot(self.page, payload, 1)
            self.assertIn("screenshotToken", payload)
            image = (self.root / "staging" / f'{payload["screenshotToken"]}.png').read_bytes()
            self.assertEqual(int.from_bytes(image[16:20], "big"), 884)
            self.assertEqual(self.page.viewport_size, original_viewport)
            self.assertEqual(await self.page.locator('html').evaluate(
                'element => getComputedStyle(element).paddingLeft'), '0px')

    async def test_padding_extends_past_the_original_document_edge(self):
        await self.show_order('costco')
        await self.page.locator('main').evaluate("""element => {
          element.style.width = '100%'; element.style.position = 'relative';
          const label = document.createElement('span'); label.textContent = 'Print invoice';
          label.style.cssText = 'position:absolute;right:-36px;top:30px;width:100px;height:20px';
          element.appendChild(label);
        }""")
        payload = order_payload('costco')
        await capture_order_screenshot(self.page, payload, 1)
        self.assertIn('screenshotToken', payload)
        image = (self.root / 'staging' / f'{payload["screenshotToken"]}.png').read_bytes()
        self.assertEqual(int.from_bytes(image[16:20], 'big'), 1476)
        self.assertEqual(await self.page.locator('[data-order-screenshot-spacer]').count(), 0)
