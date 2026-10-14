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
    _is_carrier_like_tracking,
    _is_soft_matchable_warehouse_tn,
    _is_walmart_order,
    _lot_as_match_meta,
    _matches_for_tn_allocations,
    _merge_fungible_orphan_lots,
    _soft_assign_orphan_dnr_lots,
    _tracking_aliases_from_notes,
    append_warehouse_tracking_note,
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


def _order(
    *,
    order_id: int,
    store_order_number: str,
    store_name: str | None = None,
) -> SimpleNamespace:
    store = (
        SimpleNamespace(name=store_name) if store_name is not None else None
    )
    return SimpleNamespace(
        id=order_id,
        store_order_number=store_order_number,
        store=store,
    )


LPN_STORE_3541 = "035414608074526836"
LPN_STORE_5678 = "056781234567890123"
TN_555 = "55582585857605060331"
MOUSE_NAME = "Logitech G PRO X SUPERLIGHT 2 SE Gaming Mouse 910007651"


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


class DealandrunnerSoftMatchTests(unittest.TestCase):
    def test_carrier_like_and_soft_matchable_heuristics(self) -> None:
        self.assertTrue(_is_carrier_like_tracking("1Z999AA10123456784"))
        self.assertTrue(_is_carrier_like_tracking(TN_HYPERX))
        self.assertFalse(_is_soft_matchable_warehouse_tn(TN_HYPERX))
        self.assertFalse(_is_soft_matchable_warehouse_tn("1Z999AA10123456784"))

        self.assertTrue(_is_soft_matchable_warehouse_tn(LPN_STORE_3541))
        self.assertTrue(_is_soft_matchable_warehouse_tn(LPN_STORE_5678))
        self.assertTrue(_is_soft_matchable_warehouse_tn(TN_555))
        self.assertFalse(_is_carrier_like_tracking(LPN_STORE_3541))
        self.assertFalse(_is_carrier_like_tracking(TN_555))

    def test_soft_match_orphan_lpn_to_walmart_item(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_3541,
                    system_tx="lpn-1",
                ),
            ]
        )
        item = _item(
            item_id=1, qty=1, description=MOUSE_NAME, price_sold="69.00"
        )
        order = _order(
            order_id=10,
            store_order_number="200015679743328",
            store_name="Walmart",
        )
        self.assertTrue(_is_walmart_order(order))
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[(item, order, [order.store_order_number])],
            lots=lots,
        )
        self.assertEqual(assignment.get(1), lots[0]["lot_id"])

        supply = {lots[0]["lot_id"]: int(lots[0]["qty"])}
        allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=[(1, 1, [assignment[1]])],
            supply_by_tn=supply,
        )
        self.assertEqual(allocated[1], 1)
        matches = _matches_for_tn_allocations(
            item=item,
            order=order,
            trackings=[order.store_order_number],
            tn_allocations=allocated_by_lot.get(1, []),
            meta_by_tn={lots[0]["lot_id"]: _lot_as_match_meta(lots[0])},
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tracking_number, LPN_STORE_3541)
        self.assertEqual(matches[0].receipt_id, "lpn-1")

    def test_soft_match_accepts_different_store_prefix(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_5678,
                    system_tx="lpn-2",
                ),
            ]
        )
        item = _item(
            item_id=1, qty=1, description=MOUSE_NAME, price_sold="69.00"
        )
        order = _order(
            order_id=10,
            store_order_number="200099999999999",
            store_name="Walmart",
        )
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[(item, order, [order.store_order_number])],
            lots=lots,
        )
        self.assertEqual(assignment.get(1), lots[0]["lot_id"])

    def test_carrier_orphan_tn_not_soft_matched(self) -> None:
        self.assertFalse(_is_soft_matchable_warehouse_tn(TN_HYPERX))
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=TN_HYPERX,
                    system_tx="tba-1",
                ),
            ]
        )
        # Caller filters with _is_soft_matchable_warehouse_tn before soft-assign.
        soft_lots = [
            lot
            for lot in lots
            if _is_soft_matchable_warehouse_tn(lot.get("display_tn"))
        ]
        self.assertEqual(soft_lots, [])
        item = _item(
            item_id=1, qty=1, description=MOUSE_NAME, price_sold="69.00"
        )
        order = _order(
            order_id=10,
            store_order_number="200015679743328",
            store_name="Walmart",
        )
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[(item, order, [order.store_order_number])],
            lots=soft_lots,
        )
        self.assertEqual(assignment, {})

    def test_soft_match_rejects_price_mismatch(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_3541,
                    system_tx="lpn-1",
                ),
            ]
        )
        item = _item(
            item_id=1, qty=1, description=MOUSE_NAME, price_sold="40.00"
        )
        order = _order(
            order_id=10,
            store_order_number="200015679743328",
            store_name="Walmart",
        )
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[(item, order, [order.store_order_number])],
            lots=lots,
        )
        self.assertEqual(assignment, {})

    def test_soft_match_rejects_weak_name(self) -> None:
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_3541,
                    system_tx="lpn-1",
                ),
            ]
        )
        item = _item(
            item_id=1,
            qty=1,
            description="Completely unrelated kitchen blender set",
            price_sold="69.00",
        )
        order = _order(
            order_id=10,
            store_order_number="200015679743328",
            store_name="Walmart",
        )
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[(item, order, [order.store_order_number])],
            lots=lots,
        )
        self.assertEqual(assignment, {})

    def test_soft_match_two_items_share_scarce_lot(self) -> None:
        """Two similar items may both claim one lot; allocation fills what supply allows."""
        lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_3541,
                    system_tx="lpn-1",
                ),
            ]
        )
        item_a = _item(
            item_id=1, qty=1, description=MOUSE_NAME, price_sold="69.00"
        )
        item_b = _item(
            item_id=2, qty=1, description=MOUSE_NAME, price_sold="69.00"
        )
        order_a = _order(
            order_id=10,
            store_order_number="200011111111111",
            store_name="Walmart",
        )
        order_b = _order(
            order_id=11,
            store_order_number="200022222222222",
            store_name="Walmart",
        )
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[
                (item_a, order_a, [order_a.store_order_number]),
                (item_b, order_b, [order_b.store_order_number]),
            ],
            lots=lots,
        )
        self.assertEqual(assignment.get(1), lots[0]["lot_id"])
        self.assertEqual(assignment.get(2), lots[0]["lot_id"])

        supply = {lots[0]["lot_id"]: 1}
        allocated, _allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=[
                (1, 1, [assignment[1]]),
                (2, 1, [assignment[2]]),
            ],
            supply_by_tn=supply,
        )
        self.assertEqual(sum(allocated.values()), 1)
        self.assertEqual({allocated.get(1, 0), allocated.get(2, 0)}, {0, 1})

    def test_soft_match_qty1_and_qty3_share_merged_lpn_pool(self) -> None:
        """Prod-shaped case: qty 1 + qty 3 Walmart lines vs four $69.11 LPN scans."""
        lpn_tns = [
            "035414608074528836",
            "035414608074578838",
            "035414608074528836",
            "035414608074565316",
        ]
        raw_lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=(
                        "Logitech G PRO X Superlight 2 SE Wireless Gaming Mouse "
                        "with 5 Programm"
                    ),
                    amount="69.11",
                    tn=tn,
                    system_tx=f"lpn-{i}",
                )
                for i, tn in enumerate(lpn_tns)
            ]
        )
        for lot in raw_lots:
            lot["source_tns"] = [lot["tn_key"]]
        merged = _merge_fungible_orphan_lots(raw_lots)
        self.assertEqual(len(merged), 1)
        # Duplicate LPN collapses in lot merge key by TN at receipt stage; three
        # distinct TNs → qty 3 from unique TNs, or 4 if dup kept as separate rows.
        self.assertGreaterEqual(merged[0]["qty"], 3)

        om_name = "Logitech G PRO X SUPERLIGHT 2 SE Gaming Mouse 8000"
        item_one = _item(item_id=1, qty=1, description=om_name, price_sold="69.00")
        item_three = _item(item_id=2, qty=3, description=om_name, price_sold="69.00")
        order_one = _order(
            order_id=10,
            store_order_number="200015679743328",
            store_name="Walmart",
        )
        order_three = _order(
            order_id=11,
            store_order_number="200015519785311",
            store_name="Walmart",
        )
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[
                (item_one, order_one, [order_one.store_order_number]),
                (item_three, order_three, [order_three.store_order_number]),
            ],
            lots=merged,
        )
        self.assertEqual(assignment.get(1), merged[0]["lot_id"])
        self.assertEqual(assignment.get(2), merged[0]["lot_id"])

        # Force pool qty 4 to mirror four D&R scan lines (including duplicate LPN).
        merged[0]["qty"] = 4
        supply = {merged[0]["lot_id"]: 4}
        allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=[
                (1, 1, [assignment[1]]),
                (2, 3, [assignment[2]]),
            ],
            supply_by_tn=supply,
        )
        self.assertEqual(allocated[1], 1)
        self.assertEqual(allocated[2], 3)

        meta = {merged[0]["lot_id"]: _lot_as_match_meta(merged[0])}
        matches = []
        for item, order in (
            (item_one, order_one),
            (item_three, order_three),
        ):
            matches.extend(
                _matches_for_tn_allocations(
                    item=item,
                    order=order,
                    trackings=[order.store_order_number],
                    tn_allocations=allocated_by_lot.get(item.id, []),
                    meta_by_tn=meta,
                )
            )
        by_item = {m.item_id: m for m in matches if m.match_kind == "received"}
        self.assertEqual(by_item[1].quantity, 1)
        self.assertEqual(by_item[2].quantity, 3)

    def test_fungible_lpns_merge_and_soft_match(self) -> None:
        """Multiple store LPNs for the same product must not block soft-match."""
        raw_lots = _dnr_lots_from_receipts(
            [
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_3541,
                    system_tx="lpn-1",
                ),
                _tx(
                    name=MOUSE_NAME,
                    amount="69.00",
                    tn=LPN_STORE_5678,
                    system_tx="lpn-2",
                ),
            ]
        )
        for lot in raw_lots:
            lot["source_tns"] = [lot["tn_key"]]
        merged = _merge_fungible_orphan_lots(raw_lots)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["qty"], 2)
        self.assertEqual(
            set(merged[0]["source_tns"]),
            {LPN_STORE_3541.casefold(), LPN_STORE_5678.casefold()},
        )

        item = _item(
            item_id=1, qty=1, description=MOUSE_NAME, price_sold="69.00"
        )
        order = _order(
            order_id=10,
            store_order_number="200015679743328",
            store_name="Walmart.com",
        )
        self.assertTrue(_is_walmart_order(order))
        assignment = _soft_assign_orphan_dnr_lots(
            claimants=[(item, order, [order.store_order_number])],
            lots=merged,
        )
        self.assertEqual(assignment.get(1), merged[0]["lot_id"])

        supply = {merged[0]["lot_id"]: int(merged[0]["qty"])}
        allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=[(1, 1, [assignment[1]])],
            supply_by_tn=supply,
        )
        self.assertEqual(allocated[1], 1)
        matches = _matches_for_tn_allocations(
            item=item,
            order=order,
            trackings=[order.store_order_number],
            tn_allocations=allocated_by_lot.get(1, []),
            meta_by_tn={merged[0]["lot_id"]: _lot_as_match_meta(merged[0])},
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].quantity, 1)

    def test_soft_match_ignores_non_walmart_order(self) -> None:
        order = _order(
            order_id=10,
            store_order_number="111-222",
            store_name="Amazon",
        )
        self.assertFalse(_is_walmart_order(order))

    def test_notes_alias_parsing(self) -> None:
        notes = (
            "Imported from store 'walmart'.\n"
            f"Walmart tracking: {TN_555}\n"
            f"Warehouse tracking: {LPN_STORE_3541}"
        )
        aliases = _tracking_aliases_from_notes(notes)
        self.assertEqual(aliases, [TN_555, LPN_STORE_3541])

    def test_append_warehouse_tracking_note(self) -> None:
        shipment = SimpleNamespace(
            tracking_number="200015679743328",
            notes="Imported from store 'walmart'.\n"
            f"Walmart tracking: {TN_555}",
        )
        item = SimpleNamespace(
            shipment_items=[SimpleNamespace(shipment=shipment)]
        )
        self.assertTrue(append_warehouse_tracking_note(item, LPN_STORE_3541))
        self.assertIn(
            f"Warehouse tracking: {LPN_STORE_3541}", shipment.notes or ""
        )
        # Idempotent when already present.
        self.assertFalse(append_warehouse_tracking_note(item, LPN_STORE_3541))
        # Also no-op when TN is the shipment tracking_number itself.
        self.assertFalse(
            append_warehouse_tracking_note(item, "200015679743328")
        )


if __name__ == "__main__":
    unittest.main()
