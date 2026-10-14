"""Unit tests for BFMR my-tracker purchase filtering and tracking submit."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# Allow `python -m unittest` from repo root or backend/.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.utils.buying_group_apis import bfmr as bfmr_api  # noqa: E402
from app.utils.buying_group_apis.submit_tracking import (  # noqa: E402
    _aggregate_bfmr_entries,
    build_bfmr_entry_from_purchase,
)


def _purchased_row(
    *,
    pid: int = 1704986,
    rid: int = 1997368,
    order_id: str = "200015429695661",
    qty: str | int = "2",
    tracking_number: str = "",
    status: str = "purchased",
    my_tracker_id: int = 5272755,
    sid: int | None = None,
) -> dict:
    return {
        "PID": pid,
        "RID": rid,
        "SID": sid,
        "my_tracker_id": my_tracker_id,
        "order_id": order_id,
        "qty": qty,
        "qty_received": "0",
        "status": status,
        "tracking_number": tracking_number,
        "item_name": "Apple Pencil Pro - MX2D3AM/A",
        "deal_title": "Apple Pencil Pro",
        "received": None,
    }


class BfmrParseAndFilterTests(unittest.TestCase):
    def test_parse_reserve_and_purchase_ids(self) -> None:
        row = bfmr_api._parse_tracker_row(_purchased_row())
        self.assertEqual(row.reserve_id, "1997368")
        self.assertEqual(row.purchase_id, "1704986")
        self.assertEqual(row.my_tracker_id, "5272755")
        self.assertEqual(row.order_id, "200015429695661")
        self.assertEqual(row.qty, 2)
        self.assertIsNone(row.shipment_id)
        self.assertTrue(row.needs_tracking_submit())

    def test_needs_tracking_requires_purchased_empty_tn(self) -> None:
        with_tn = bfmr_api._parse_tracker_row(
            _purchased_row(tracking_number="1Z999AA10123456784")
        )
        self.assertFalse(with_tn.needs_tracking_submit())

        reserved = bfmr_api._parse_tracker_row(_purchased_row(status="reserved"))
        self.assertFalse(reserved.needs_tracking_submit())

        returned = bfmr_api._parse_tracker_row(_purchased_row(status="return"))
        self.assertFalse(returned.needs_tracking_submit())

    def test_list_purchases_needing_tracking_filters_client_side(self) -> None:
        payload = {
            "success": True,
            "status": 200,
            "data": {
                "my_tracker": [
                    _purchased_row(),
                    _purchased_row(
                        pid=1704984,
                        rid=1997367,
                        order_id="200015175137769",
                        my_tracker_id=5272752,
                    ),
                    _purchased_row(
                        pid=1605710,
                        rid=1865804,
                        order_id="",
                        status="return",
                        tracking_number="1310418088",
                        qty="1",
                        sid=2958638,
                    ),
                    _purchased_row(
                        pid=99,
                        rid=88,
                        order_id="111",
                        tracking_number="TBA123",
                    ),
                ],
                "paging": {"page": 1, "pages": 1},
            },
        }
        raw_text = json.dumps(payload).encode("utf-8")

        class _Resp:
            status = 200

            def read(self) -> bytes:
                return raw_text

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            result = bfmr_api.list_purchases_needing_tracking(
                base_url="https://api.bfmr.com",
                api_key="key",
                api_secret="secret",
            )
        self.assertEqual(len(result.rows), 2)
        by_order = bfmr_api.index_purchases_needing_tracking_by_order(result.rows)
        self.assertIn("200015429695661", by_order)
        self.assertIn("200015175137769", by_order)
        self.assertNotIn("111", by_order)


class BfmrSubmitPayloadTests(unittest.TestCase):
    def test_entry_to_tracker_data_create_shipment(self) -> None:
        entry = bfmr_api.BfmrTrackingEntry(
            reserve_id="1997368",
            purchase_id="1704986",
            order_no="200015429695661",
            tracking_number="1Z999AA10123456784",
            qty=2,
        )
        payload = bfmr_api._entry_to_tracker_data(entry)
        self.assertEqual(
            payload,
            {
                "reserve_id": "1997368",
                "purchase_id": "1704986",
                "order_no": "200015429695661",
                "tracking_number": "1Z999AA10123456784",
                "qty": 2,
                "shipment_id": None,
            },
        )

    def test_submit_trackings_posts_tracker_data(self) -> None:
        response = {
            "message": "Data updated successfully!",
            "reservations_response": {"valid_items": [], "invalid_items": []},
        }
        raw_text = json.dumps(response).encode("utf-8")
        captured: dict[str, object] = {}

        class _Resp:
            status = 200

            def read(self) -> bytes:
                return raw_text

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def _urlopen(req: object, timeout: float = 0) -> _Resp:
            captured["url"] = getattr(req, "full_url", None) or getattr(req, "get_full_url")()
            captured["method"] = getattr(req, "method", None) or req.get_method()  # type: ignore[union-attr]
            captured["data"] = req.data  # type: ignore[attr-defined]
            captured["headers"] = dict(req.headers)  # type: ignore[attr-defined]
            return _Resp()

        with mock.patch("urllib.request.urlopen", side_effect=_urlopen):
            result = bfmr_api.submit_trackings(
                base_url="https://api.bfmr.com",
                api_key="KEY",
                api_secret="SECRET",
                trackings=[
                    bfmr_api.BfmrTrackingEntry(
                        reserve_id="1997368",
                        purchase_id="1704986",
                        order_no="200015429695661",
                        tracking_number="1Z999AA10123456784",
                        qty=2,
                    )
                ],
            )

        self.assertTrue(result.success)
        self.assertEqual(result.succeeded, ["1Z999AA10123456784"])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://api.bfmr.com/api/v2/my-tracker")
        body = json.loads(captured["data"].decode("utf-8"))  # type: ignore[union-attr]
        self.assertEqual(
            body["tracker_data"][0]["tracking_number"],
            "1Z999AA10123456784",
        )
        headers = {k.lower(): v for k, v in (captured["headers"] or {}).items()}  # type: ignore[union-attr]
        self.assertEqual(headers.get("api-key") or headers.get("API-KEY"), "KEY")

    def test_submit_trackings_marks_invalid_items_failed(self) -> None:
        response = {
            "message": "Data updated successfully!",
            "reservations_response": {
                "valid_items": [
                    {"tracking_number": "GOODTN", "purchase_id": "1"},
                ],
                "invalid_items": [
                    {
                        "tracking_number": "BADTN",
                        "purchase_id": "2",
                        "message": "Purchase is not active",
                    }
                ],
            },
        }
        raw_text = json.dumps(response).encode("utf-8")

        class _Resp:
            status = 200

            def read(self) -> bytes:
                return raw_text

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            result = bfmr_api.submit_trackings(
                base_url="https://api.bfmr.com",
                api_key="KEY",
                api_secret="SECRET",
                trackings=[
                    bfmr_api.BfmrTrackingEntry(
                        reserve_id="10",
                        purchase_id="1",
                        order_no="ORD-GOOD",
                        tracking_number="GOODTN",
                        qty=1,
                    ),
                    bfmr_api.BfmrTrackingEntry(
                        reserve_id="20",
                        purchase_id="2",
                        order_no="ORD-BAD",
                        tracking_number="BADTN",
                        qty=1,
                    ),
                ],
            )

        self.assertFalse(result.success)
        self.assertEqual(result.succeeded, ["GOODTN"])
        self.assertEqual(result.failed, ["BADTN"])
        self.assertIn("Purchase is not active", result.message)


class BfmrMatchAggregateTests(unittest.TestCase):
    def test_build_entry_caps_qty_at_purchase(self) -> None:
        purchase = bfmr_api._parse_tracker_row(_purchased_row(qty="2"))
        items = [
            SimpleNamespace(quantity=3),
            SimpleNamespace(quantity=1),
        ]
        entry = build_bfmr_entry_from_purchase(
            purchase,
            tracking="1ZAAA",
            items=items,  # type: ignore[arg-type]
        )
        self.assertEqual(entry.qty, 2)
        self.assertEqual(entry.reserve_id, "1997368")
        self.assertEqual(entry.purchase_id, "1704986")
        self.assertEqual(entry.order_no, "200015429695661")
        self.assertIsNone(entry.shipment_id)

    def test_aggregate_matches_store_order_number(self) -> None:
        purchase = bfmr_api._parse_tracker_row(_purchased_row(qty="2"))
        purchases = {"200015429695661": purchase}
        item = SimpleNamespace(id=1, quantity=2)
        order = SimpleNamespace(store_order_number="200015429695661")
        entries, matched, skips = _aggregate_bfmr_entries(
            [(item, order, "1ZAAA")],  # type: ignore[list-item]
            purchases,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].tracking_number, "1ZAAA")
        self.assertEqual(entries[0].qty, 2)
        self.assertEqual(len(matched), 1)
        self.assertEqual(skips, [])

    def test_aggregate_skips_unmatched_order(self) -> None:
        purchase = bfmr_api._parse_tracker_row(_purchased_row())
        purchases = {"200015429695661": purchase}
        item = SimpleNamespace(id=1, quantity=1)
        order = SimpleNamespace(store_order_number="DOES-NOT-EXIST")
        entries, matched, skips = _aggregate_bfmr_entries(
            [(item, order, "1ZAAA")],  # type: ignore[list-item]
            purchases,
        )
        self.assertEqual(entries, [])
        self.assertEqual(matched, [])
        self.assertTrue(any("DOES-NOT-EXIST" in s for s in skips))

    def test_aggregate_splits_qty_across_trackings(self) -> None:
        purchase = bfmr_api._parse_tracker_row(_purchased_row(qty="3"))
        purchases = {"200015429695661": purchase}
        item_a = SimpleNamespace(id=1, quantity=1)
        item_b = SimpleNamespace(id=2, quantity=2)
        order = SimpleNamespace(store_order_number="200015429695661")
        entries, matched, skips = _aggregate_bfmr_entries(
            [
                (item_a, order, "TN-A"),  # type: ignore[list-item]
                (item_b, order, "TN-B"),  # type: ignore[list-item]
            ],
            purchases,
        )
        self.assertEqual(len(entries), 2)
        by_tn = {e.tracking_number: e.qty for e in entries}
        self.assertEqual(by_tn["TN-A"], 1)
        self.assertEqual(by_tn["TN-B"], 2)
        self.assertEqual(len(matched), 2)
        self.assertEqual(skips, [])


if __name__ == "__main__":
    unittest.main()
