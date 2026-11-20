"""Best Buy guest order parsing, approval, and saved-link refresh."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user, get_current_user_optional
from app.browser_automation import best_buy_import
from app.browser_automation.best_buy_api import enrich_order
from app.database import Base, get_db
from app.models import Order, Shipment, User
from app.routers.store_imports import router
from app.routers.orders import router as orders_router
from app.schemas.order import OrderRead

_SOURCE_URL = "https://click.emailinfo2.bestbuy.com/?qs=ExampleToken0123456"
_ORDER_NUMBER = "BBY03-123456789012"
_API = "/integrations/stores/orders"


def _api_response():
    line_ids = [f"{_ORDER_NUMBER}-A-{index}" for index in (1, 2)]
    return {"order": {
        "userOrderId": _ORDER_NUMBER,
        "items": [{"id": line, "sku": "12345678", "quantity": 1, "originalQuantity": 1,
                   "thirdPartyOrderId": f"seller-{index}", "fulfillment": {"addressId": "ship"},
                   "metadata": {"catalog": {"manufacturer": {"modelNumber": "EXAMPLE"}}}}
                  for index, line in enumerate(line_ids)],
        "groups": {"fulfillmentGroups": [{"groupId": "g1", "members": [{"itemKey": line} for line in line_ids]}]},
        "price": {"orderTotal": 215.0, "productTotal": 200.0, "shippingTotal": 0, "totalSalesTax": 15},
        "payments": [{"displayCreditCardNumber": "****1234", "creditCardType": "Visa", "amount": 215,
                      "secureVaultToken": "private-payment-token", "authorizationCode": "private-auth-code",
                      "expirationMonth": "11", "expirationYear": "2030"}],
        "user": {"firstName": "Example", "lastName": "Customer", "emailAddress": "ORDER@example.test",
                 "visitorToken": "private-visitor-token",
                 "addresses": [{"id": "ship", "addressLine1": "100 Example St", "phoneNumber": "2065550123"},
                               {"id": "bill", "addressLine1": "Billing address outside import"}]},
    }}


def _line_item():
    return """
      <article>
        <a href="https://www.bestbuy.com/product/example/sku/12345678?ref=order">Example Laptop</a>
        <div><span>Model:</span><span>EXAMPLE</span></div>
        <div><span>SKU:</span><span>12345678</span></div>
        <div><span>Quantity:</span><span>1</span></div>
        <div><span>Item Total:</span><span>$100.00</span></div>
        <div><span>Product Price:</span><span>$100.00</span></div>
        <div><span>Shipping:</span><span>FREE</span></div>
      </article>
    """


def _fixture(tracking=None):
    if tracking:
        status = "Delivered on Nov 22, 2026"
        tracking_markup = f'<a href="https://www.ups.com/track?tracknum={tracking}">{tracking}</a>'
    else:
        status = "We'll provide tracking when it's available."
        tracking_markup = "<span>Tracking info will be made available once it's known</span>"
    return f"""
      <header>
        <h1>Order Details</h1>
        <p>Purchase Date: Nov 20, 2026</p>
        <p>Order Number: {_ORDER_NUMBER}</p>
        <p>Total: $215.00</p>
      </header>
      <div>
        <div>
          <div><h2>Payment Method</h2></div>
          <div><p>Example card</p><p>****1234</p></div>
        </div>
        <div>
          <div><h2>Order Summary</h2></div>
          <div><span>Product Total:</span><span>$200.00</span></div>
          <div><span>Shipping:</span><span>FREE</span></div>
          <div><span>Sales Tax, Fees &amp; Surcharges:</span><span>$15.00</span></div>
          <div><span>Order Total</span><span>$215.00</span></div>
        </div>
      </div>
      <section>
        <p>Preparing your order</p>
        <div><span>Shipping Address</span><div>Example Customer<br>100 Example St<br>Sample City, WA 98101 US</div></div>
        <div><h2>Tracking info</h2><p>Tracking Number: Pending</p></div>
        {_line_item()}
      </section>
      <section>
        <p>{status}</p>
        <div><span>Shipping Address</span><div>Example Customer<br>100 Example St<br>Sample City, WA 98101 US</div></div>
        <div><h2>Tracking info</h2><p><span>Tracking Number:</span>{tracking_markup}</p></div>
        {_line_item()}
      </section>
    """


class BestBuyImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(_fixture())
            cls.details = page.evaluate(best_buy_import._EXTRACT_DETAILS)
            cls.pending = best_buy_import.normalize_order(cls.details)
            page.set_content(_fixture("1Z1234567890123456"))
            cls.tracked = best_buy_import.normalize_order(page.evaluate(best_buy_import._EXTRACT_DETAILS))
            browser.close()

    def test_extracts_repeated_lines_address_totals_and_package_tracking(self):
        self.assertEqual(self.tracked.externalOrder, {"id": _ORDER_NUMBER, "orderDate": "2026-11-20"})
        self.assertEqual(self.tracked.shippingAddress, {
            "fullName": "Example Customer", "addressLine1": "100 Example St",
            "city": "Sample City", "state": "WA", "postalCode": "98101", "country": "US",
        })
        self.assertIsNone(self.tracked.customer)
        self.assertEqual(self.tracked.totals, {
            "itemCount": 2, "subtotal": 200.0, "shipping": 0.0, "tax": 15.0, "grandTotal": 215.0,
        })
        self.assertEqual(self.tracked.paymentMethods[0]["last4"], "1234")
        first, second = self.tracked.items
        self.assertEqual(first["name"], second["name"])
        self.assertNotEqual(first["logicalItemId"], second["logicalItemId"])
        self.assertNotEqual(first["shipments"], second["shipments"])
        self.assertEqual(first["productUrl"], "https://www.bestbuy.com/product/example/sku/12345678")
        self.assertEqual(self.tracked.shipments[1]["trackingNumber"], "1Z1234567890123456")
        self.assertEqual(self.tracked.shipments[1]["deliveryDate"], "2026-11-22")
        self.assertIsNone(self.tracked.shipments[0]["trackingNumber"])

    def test_normalizes_line_tax_and_contact_fields_for_shared_review(self):
        details = deepcopy(self.details)
        details["rows"][0].update(quantity="2", itemTotal="$200.00", productPrice="$100.00")
        details["subtotal"] = "$300.00"
        details["total"] = "$315.00"
        for group in details["groups"]:
            group["contact"] = {"email": "ORDER@example.test", "phone": "(206) 555-0123"}
        payload = best_buy_import.normalize_order(details)
        self.assertEqual(payload.customer, {"email": "order@example.test"})
        self.assertEqual(payload.shippingAddress["phoneNumber"], "+12065550123")
        self.assertEqual(payload.items[0]["pricing"], {
            "unitPrice": 100.0, "linePrice": 200.0, "lineTotal": 200.0, "shipping": 0.0, "tax": 10.0,
        })
        self.assertEqual(payload.items[1]["pricing"]["tax"], 5.0)

    def test_preview_approval_and_tracking_refresh(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)
        app = FastAPI()
        app.include_router(router)
        app.include_router(orders_router)
        same_package = self.pending.model_copy(deep=True)
        same_package.items[1]["shipments"] = deepcopy(same_package.items[0]["shipments"])
        api_response = _api_response()
        mismatch = deepcopy(api_response)
        mismatch["order"]["userOrderId"] = "BBY03-000000000000"
        self.assertIs(enrich_order(same_package, mismatch), same_package)
        enriched = enrich_order(same_package, api_response)
        self.assertEqual(enriched.customer["email"], "order@example.test")
        self.assertEqual(enriched.shippingAddress["phoneNumber"], "+12065550123")
        self.assertEqual(enriched.items[1]["logicalItemId"], f"{_ORDER_NUMBER}-A-2")
        for secret in ("private-payment-token", "private-auth-code", "private-visitor-token", "Billing address outside import", "expirationMonth"):
            self.assertNotIn(secret, enriched.model_dump_json())

        def override_db():
            with session() as db:
                yield db

        def override_user(request: Request):
            return User(id=2 if request.headers.get("X-Other-User") else 1, username="test", role="user")

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_current_user_optional] = override_user
        with TestClient(app) as client, patch.object(
            best_buy_import, "fetch_order", side_effect=[self.pending, enriched, enrich_order(self.tracked, api_response), self.tracked],
        ) as browser:
            self.assertEqual(client.post(f"{_API}/best-buy-preview", json={
                "url": "https://click.emailinfo2.bestbuy.com@other.example/orders",
            }).status_code, 400)
            browser.assert_not_awaited()

            created = client.post(f"{_API}/best-buy-preview", json={"url": _SOURCE_URL})
            self.assertEqual(created.status_code, 200, created.text)
            token = created.json()["token"]
            preview_url = f"{_API}/bulk-session/{token}"
            self.assertEqual(client.get(preview_url, headers={"X-Other-User": "1"}).status_code, 404)
            preview = client.get(preview_url)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertNotIn(_SOURCE_URL, preview.text)
            self.assertEqual(preview.headers["Cache-Control"], "no-store")
            data = preview.json()
            self.assertEqual(len(data["orders"]), 1)
            payload, = data["orders"]
            diff_body = {"orders": data["orders"], "session_token": token}
            diff = client.post(f"{_API}/diff-bulk", json=diff_body)
            self.assertEqual(diff.json()["diffs"], [{"is_existing_order": False}])
            with session() as db:
                self.assertEqual(db.query(Order).count(), 0)

            payload["items"][1]["name"] = "Renamed laptop"
            payload["items"][1]["pricing"]["unitPrice"] = 99
            approve = {"payload": payload, "session_token": token, "item_payouts": [85, 90]}
            invalid = {**approve, "payload": {**payload, "externalOrder": {"id": "BBY03-000000000000"}}}
            self.assertEqual(client.post(f"{_API}/apply", json=invalid).status_code, 409)
            saved = client.post(f"{_API}/apply", json=approve)
            self.assertEqual(saved.status_code, 200, saved.text)
            order_id = saved.json()["order_id"]
            self.assertEqual(client.post(f"{_API}/apply", json=approve).json()["order_id"], order_id)
            with session() as db:
                order = db.get(Order, order_id)
                self.assertEqual(order.best_buy_order_url, _SOURCE_URL)
                self.assertEqual(order.store.name, "Best Buy")
                self.assertEqual(order.sales_tax, Decimal("15.00"))
                self.assertEqual(len(order.items), 2)
                self.assertEqual({item.price_sold for item in order.items}, {Decimal("85"), Decimal("90")})
                self.assertEqual({item.sales_tax for item in order.items}, {Decimal("7.50")})
                self.assertEqual(db.query(Shipment).count(), 0)
                order_read = OrderRead.model_validate(order)
                self.assertTrue(order_read.has_best_buy_order_link)
                self.assertNotIn(_SOURCE_URL, order_read.model_dump_json())
                order.best_buy_order_url = None
                db.commit()
            diff = client.post(f"{_API}/diff-bulk", json=diff_body).json()["diffs"][0]
            self.assertTrue(diff["source_url_to_save"])
            self.assertEqual(client.post(f"{_API}/apply", json=approve).status_code, 200)

            backfill = client.post(f"{_API}/{order_id}/best-buy-tracking-preview")
            backfill_token = backfill.json()["token"]
            newer, = client.get(f"{_API}/bulk-session/{backfill_token}").json()["orders"]
            diff = client.post(f"{_API}/diff-bulk", json={
                "orders": [newer], "session_token": backfill_token,
            }).json()["diffs"][0]
            self.assertTrue(diff["source_details_to_save"])
            newer["bestBuyDetails"] = {"injected": "untrusted client data"}
            updated = client.post(f"{_API}/apply", json={"payload": newer, "session_token": backfill_token})
            self.assertEqual(updated.status_code, 200, updated.text)
            details = client.get(f"/orders/{order_id}/best-buy-details").json()
            self.assertEqual(details["customer"]["email"], "order@example.test")
            self.assertEqual(details["payments"][0]["last4"], "1234")
            self.assertNotIn("injected", details)
            listing = client.get(f"/orders/{order_id}").json()
            self.assertTrue(listing["has_best_buy_details"])
            self.assertNotIn("best_buy_details", listing)

            refresh = client.post(f"{_API}/{order_id}/best-buy-tracking-preview")
            self.assertEqual(refresh.status_code, 200, refresh.text)
            refresh_token = refresh.json()["token"]
            refreshed, = client.get(f"{_API}/bulk-session/{refresh_token}").json()["orders"]
            self.assertEqual(refreshed["shipments"][1]["trackingNumber"], "1Z1234567890123456")
            diff = client.post(f"{_API}/diff-bulk", json={
                "orders": [refreshed], "session_token": refresh_token,
            }).json()["diffs"][0]
            self.assertNotIn("source_url_to_save", diff)
            self.assertEqual(diff["shipments"]["added"][0]["tracking_number"], "1Z1234567890123456")
            update = client.post(f"{_API}/apply", json={
                "payload": refreshed, "session_token": refresh_token,
            })
            self.assertEqual(update.status_code, 200, update.text)
            with session() as db:
                self.assertEqual(db.query(Order).count(), 1)
                order = db.get(Order, order_id)
                self.assertEqual(len(order.items), 2)
                self.assertTrue(order.best_buy_details)
                self.assertEqual(order.best_buy_details["items"][1]["legacyLogicalItemId"], "12345678:2")
                self.assertEqual(order.best_buy_details["shipments"][1]["lineIds"], [f"{_ORDER_NUMBER}-A-2"])
                shipment, = db.query(Shipment).all()
                self.assertEqual(shipment.tracking_number, "1Z1234567890123456")
                self.assertEqual(len(shipment.shipment_items), 1)
                item = shipment.shipment_items[0].item
                self.assertEqual(item.description, "Renamed laptop")
                self.assertEqual(item.import_line_key, f"{_ORDER_NUMBER}-A-2")
                self.assertEqual(item.price_sold, Decimal("90.00"))
                self.assertEqual(item.price_paid, Decimal("99.00"))
            fallback = client.post(f"{_API}/{order_id}/best-buy-tracking-preview")
            fallback_token = fallback.json()["token"]
            fallback_orders = client.get(f"{_API}/bulk-session/{fallback_token}").json()["orders"]
            self.assertEqual(client.post(f"{_API}/apply", json={
                "payload": fallback_orders[0], "session_token": fallback_token,
            }).status_code, 200)
            with session() as db:
                self.assertEqual(len(db.get(Order, order_id).items), 2)
            self.assertEqual([call.args[0] for call in browser.await_args_list], [_SOURCE_URL] * 4)


if __name__ == "__main__":
    unittest.main()
