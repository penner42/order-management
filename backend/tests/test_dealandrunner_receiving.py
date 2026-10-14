"""Unit tests for Deal & Runner runner-transactions receiving / lot matching."""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

# Allow `python -m unittest` from repo root or backend/.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.utils.buying_group_apis import dealandrunner as dnr_api  # noqa: E402
from app.utils.buying_group_apis.check_receiving import (  # noqa: E402
    _allocate_received_across_shared_trackings,
    _assign_items_to_lots,
    _dnr_lots_from_receipts,
    _lot_as_match_meta,
    _matches_for_tn_allocations,
)


TN_HYPERX = "TBA335323251475"
TN_MIXED = "TBA999000111222"

HYPERX_NAME = (
    "HyperX Cloud Alpha - Gaming Headset, Dual Chamber Drivers, "
    "Legendary Comfort, Aluminum Frame, Detachable Microphone, Works on PC, "
    "PS4, PS5, Xbox One, Xbox Series X|S, Nintendo Switch and Mobile – Red"
)
LOGITECH_NAME = "Logitech -G733 LIGHTSPEED Wireless Gaming Headset with LIGHTSYNC RGB- Black"


def _tx(
    *,
    name: str,
    amount: str,
    tn: str,
    system_tx: str,
    timestamp: str = "2026-10-12T18:19:23.394851Z",
    entry_type_id: int = 1,
) -> dnr_api.DealandrunnerReceivedRow:
    return dnr_api.DealandrunnerReceivedRow(
        tracking_numbers=[tn],
        amount=Decimal(amount),
        product_name=name,
        system_transaction_id=system_tx,
        timestamp=timestamp,
        entry_type_id=entry_type_id,
    )


def _item(
    *,
    item_id: int,
    qty: int,
    description: str,
    price_sold: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        quantity=qty,
        description=description,
        price_sold=Decimal(price_sold),
        receipt_id=None,
    )


def _order(*, order_id: int, store_order_number: str) -> SimpleNamespace:
    return SimpleNamespace(id=order_id, store_order_number=store_order_number)


class DealandrunnerParseTests(unittest.TestCase):
    def test_parse_strips_tracking_whitespace(self) -> None:
        row = dnr_api._parse_received_row(
            {
                "amount": 37.75,
                "productName": HYPERX_NAME,
                "systemTransactionId": "f2a7506a-00c8-45cd-9a7d-37d50568d1c9",
                "timestamp": "2026-10-12T18:19:23.394851Z",
                "trackingNumbers": ["TBA335323251475 "],
                "id": "00000000-0000-0000-0000-000000000000",
                "entryTypeId": 1,
            }
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.tracking_numbers, [TN_HYPERX])
        self.assertEqual(row.amount, Decimal("37.75"))
        self.assertEqual(
            row.system_transaction_id, "f2a7506a-00c8-45cd-9a7d-37d50568d1c9"
        )
        self.assertEqual(row.product_name, HYPERX_NAME)

    def test_parse_skips_rows_without_tracking(self) -> None:
        row = dnr_api._parse_received_row(
            {
                "amount": 10,
                "productName": "No TN",
                "systemTransactionId": "abc",
                "trackingNumbers": ["  "],
            }
        )
        self.assertIsNone(row)

    def test_parse_falls_back_to_warehouse_item_id(self) -> None:
        row = dnr_api._parse_received_row(
            {
                "amount": 1,
                "productName": "X",
                "trackingNumbers": ["TN1"],
                "referenceWarehouseItemId": "wh-123",
                "entryTypeId": 1,
            }
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.system_transaction_id, "wh-123")

    def test_list_keeps_only_receiving_entry_type(self) -> None:
        """Payments (non-1 entryTypeId) must not become receiving supply."""
        receiving = dnr_api._parse_received_row(
            {
                "amount": 37.75,
                "productName": HYPERX_NAME,
                "systemTransactionId": "recv-1",
                "trackingNumbers": [TN_HYPERX],
                "entryTypeId": 1,
            }
        )
        payment = dnr_api._parse_received_row(
            {
                "amount": -100.0,
                "productName": None,
                "systemTransactionId": "pay-1",
                "trackingNumbers": [TN_HYPERX],
                "entryTypeId": 2,
            }
        )
        self.assertIsNotNone(receiving)
        self.assertIsNotNone(payment)
        assert receiving is not None and payment is not None
        self.assertEqual(receiving.entry_type_id, dnr_api.RECEIVING_ENTRY_TYPE_ID)
        self.assertNotEqual(payment.entry_type_id, dnr_api.RECEIVING_ENTRY_TYPE_ID)
        # Mirror list_runner_transactions filter.
        kept = [
            r
            for r in (receiving, payment)
            if r.entry_type_id == dnr_api.RECEIVING_ENTRY_TYPE_ID
        ]
        self.assertEqual(kept, [receiving])


class DealandrunnerLotMatchingTests(unittest.TestCase):
    def test_n_rows_aggregate_qty_on_same_tn(self) -> None:
        """Each ledger line is qty 1; two HyperX rows on one TN → supply 2."""
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=HYPERX_NAME,
                    amount="37.75",
                    tn=TN_HYPERX,
                    system_tx="tx-1",
                ),
                _tx(
                    name=HYPERX_NAME,
                    amount="37.75",
                    tn=TN_HYPERX,
                    system_tx="tx-2",
                ),
            ]
        )
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]["qty"], 2)
        self.assertEqual(lots[0]["unit_price"], Decimal("37.75"))
        self.assertEqual(lots[0]["display_tn"], TN_HYPERX)
        self.assertEqual(lots[0]["receipt_ids"], ["tx-1", "tx-2"])

    def test_mixed_products_on_shared_tn_form_separate_lots(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=HYPERX_NAME,
                    amount="37.75",
                    tn=TN_MIXED,
                    system_tx="tx-h1",
                ),
                _tx(
                    name=HYPERX_NAME,
                    amount="37.75",
                    tn=TN_MIXED,
                    system_tx="tx-h2",
                ),
                _tx(
                    name=LOGITECH_NAME,
                    amount="64.76",
                    tn=TN_MIXED,
                    system_tx="tx-l1",
                ),
                _tx(
                    name=LOGITECH_NAME,
                    amount="64.76",
                    tn=TN_MIXED,
                    system_tx="tx-l2",
                ),
                _tx(
                    name=LOGITECH_NAME,
                    amount="64.76",
                    tn=TN_MIXED,
                    system_tx="tx-l3",
                ),
            ]
        )
        self.assertEqual(len(lots), 2)
        by_name = {lot["received_item"]: lot for lot in lots}
        self.assertEqual(by_name[HYPERX_NAME]["qty"], 2)
        self.assertEqual(by_name[LOGITECH_NAME]["qty"], 3)

    def test_shared_tracking_assigns_correct_payouts(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(name=HYPERX_NAME, amount="37.75", tn=TN_MIXED, system_tx="h1"),
                _tx(name=HYPERX_NAME, amount="37.75", tn=TN_MIXED, system_tx="h2"),
                _tx(name=LOGITECH_NAME, amount="64.76", tn=TN_MIXED, system_tx="l1"),
                _tx(name=LOGITECH_NAME, amount="64.76", tn=TN_MIXED, system_tx="l2"),
                _tx(name=LOGITECH_NAME, amount="64.76", tn=TN_MIXED, system_tx="l3"),
            ]
        )
        lots_by_tn = {TN_MIXED.casefold(): lots}
        lots_by_id = {lot["lot_id"]: lot for lot in lots}
        meta_by_lot = {lid: _lot_as_match_meta(lot) for lid, lot in lots_by_id.items()}

        hyperx = _item(item_id=1, qty=2, description=HYPERX_NAME, price_sold="37.75")
        logitech = _item(
            item_id=2, qty=3, description=LOGITECH_NAME, price_sold="64.76"
        )
        trackings = [TN_MIXED]
        claimants = [(hyperx, trackings), (logitech, trackings)]

        assignment = _assign_items_to_lots(
            claimants=claimants, lots_by_tn=lots_by_tn
        )
        self.assertEqual(len(assignment), 2)

        hyperx_lot_ids = {
            lot["lot_id"] for lot in lots if lot["received_item"] == HYPERX_NAME
        }
        logitech_lot_ids = {
            lot["lot_id"] for lot in lots if lot["received_item"] == LOGITECH_NAME
        }
        self.assertIn(assignment[1], hyperx_lot_ids)
        self.assertIn(assignment[2], logitech_lot_ids)

        supply_by_lot = {lid: int(lot["qty"]) for lid, lot in lots_by_id.items()}
        item_demands = [
            (item.id, item.quantity, [assignment[item.id]])
            for item, _tns in claimants
        ]
        allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=item_demands,
            supply_by_tn=supply_by_lot,
        )
        self.assertEqual(allocated[1], 2)
        self.assertEqual(allocated[2], 3)

        order = _order(order_id=10, store_order_number="111-222-333")
        matches = []
        for item in (hyperx, logitech):
            matches.extend(
                _matches_for_tn_allocations(
                    item=item,
                    order=order,
                    trackings=trackings,
                    tn_allocations=allocated_by_lot.get(item.id, []),
                    meta_by_tn=meta_by_lot,
                )
            )
        by_item = {m.item_id: m for m in matches}
        self.assertEqual(by_item[1].received_price, Decimal("37.75"))
        self.assertEqual(by_item[1].received_ext_total, Decimal("75.50"))
        self.assertFalse(by_item[1].amount_discrepancy)
        self.assertEqual(by_item[2].received_price, Decimal("64.76"))
        self.assertEqual(by_item[2].received_ext_total, Decimal("194.28"))
        self.assertFalse(by_item[2].amount_discrepancy)

    def test_under_received_leaves_remainder(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(name=HYPERX_NAME, amount="37.75", tn=TN_HYPERX, system_tx="a"),
                _tx(name=HYPERX_NAME, amount="37.75", tn=TN_HYPERX, system_tx="b"),
            ]
        )
        lots_by_tn = {TN_HYPERX.casefold(): lots}
        lots_by_id = {lot["lot_id"]: lot for lot in lots}
        meta_by_lot = {lid: _lot_as_match_meta(lot) for lid, lot in lots_by_id.items()}

        item = _item(item_id=1, qty=5, description=HYPERX_NAME, price_sold="37.75")
        trackings = [TN_HYPERX]
        assignment = _assign_items_to_lots(
            claimants=[(item, trackings)], lots_by_tn=lots_by_tn
        )
        supply_by_lot = {lid: int(lot["qty"]) for lid, lot in lots_by_id.items()}
        allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=[(item.id, item.quantity, [assignment[item.id]])],
            supply_by_tn=supply_by_lot,
        )
        self.assertEqual(allocated[1], 2)
        order = _order(order_id=1, store_order_number="x")
        matches = _matches_for_tn_allocations(
            item=item,
            order=order,
            trackings=trackings,
            tn_allocations=allocated_by_lot.get(item.id, []),
            meta_by_tn=meta_by_lot,
        )
        kinds = {m.match_kind for m in matches}
        self.assertIn("received", kinds)
        self.assertIn("not_received", kinds)
        received = next(m for m in matches if m.match_kind == "received")
        remainder = next(m for m in matches if m.match_kind == "not_received")
        self.assertEqual(received.quantity, 2)
        self.assertEqual(remainder.quantity, 3)


if __name__ == "__main__":
    unittest.main()
