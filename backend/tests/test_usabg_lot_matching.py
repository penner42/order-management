"""Unit tests for USABG multi-product shared-tracking lot matching."""
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

from app.utils.buying_group_apis import usabg as usabg_api  # noqa: E402
from app.utils.buying_group_apis.check_receiving import (  # noqa: E402
    _allocate_received_across_shared_trackings,
    _assign_items_to_lots,
    _is_zero_payout,
    _lot_as_match_meta,
    _matches_for_tn_allocations,
    _name_similarity,
    _score_item_to_lot,
    _usabg_lots_from_receipts,
)


TN = "TBA334270536722"

WATCH_NAME = (
    "Apple Watch SE 3 (GPS) 40mm Starlight Aluminum Case with "
    "Starlight Sport Band - S/M - Starlight - (2025)"
)
PENCIL_NAME_USABG = "Apple Pencil Pro - White"
PENCIL_NAME_LOCAL = "Apple Pencil Pro: Latest Model"


def _row(
    *,
    name: str,
    qty: int,
    unit: str,
    sku: str,
    tn: str = TN,
    po: str = "2146210",
) -> usabg_api.UsabgReceivedRow:
    unit_d = Decimal(unit)
    return usabg_api.UsabgReceivedRow(
        po_number=po,
        purchase_id="ALPEN28 10.07.2026",
        tracking_number=tn,
        quantity=qty,
        unit_price=unit_d,
        ext_total=(unit_d * qty).quantize(Decimal("0.01")),
        received_status="Pending",
        receiving_status="Received",
        scanned_at="09-07-2026 12:29:50",
        received_item=name,
        sku=sku,
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


class UsabgLotMatchingTests(unittest.TestCase):
    def test_name_similarity_distinguishes_watch_vs_pencil(self) -> None:
        self.assertGreater(
            _name_similarity(WATCH_NAME, WATCH_NAME),
            _name_similarity(WATCH_NAME, PENCIL_NAME_USABG),
        )
        self.assertGreater(
            _name_similarity(PENCIL_NAME_LOCAL, PENCIL_NAME_USABG),
            _name_similarity(PENCIL_NAME_LOCAL, WATCH_NAME),
        )

    def test_lots_from_dual_product_box(self) -> None:
        lots = _usabg_lots_from_receipts(
            [
                _row(name=WATCH_NAME, qty=4, unit="189.99", sku="MEH34LW/A"),
                _row(name=PENCIL_NAME_USABG, qty=4, unit="96.00", sku="MX2D3AM/A"),
            ]
        )
        self.assertEqual(len(lots), 2)
        by_sku = {lot["sku"]: lot for lot in lots}
        self.assertEqual(by_sku["MEH34LW/A"]["qty"], 4)
        self.assertEqual(by_sku["MEH34LW/A"]["unit_price"], Decimal("189.99"))
        self.assertEqual(by_sku["MX2D3AM/A"]["qty"], 4)
        self.assertEqual(by_sku["MX2D3AM/A"]["unit_price"], Decimal("96.00"))

    def test_price_score_prefers_dollar_drift_over_wrong_product(self) -> None:
        watch_lot = {
            "unit_price": Decimal("189.99"),
            "received_item": WATCH_NAME,
        }
        pencil_lot = {
            "unit_price": Decimal("96.00"),
            "received_item": PENCIL_NAME_USABG,
        }
        pencil_item = _item(
            item_id=1,
            qty=3,
            description=PENCIL_NAME_LOCAL,
            price_sold="97.00",
        )
        watch_score = _score_item_to_lot(pencil_item, watch_lot)
        pencil_score = _score_item_to_lot(pencil_item, pencil_lot)
        self.assertIsNotNone(pencil_score)
        # Watch is far outside ±$2 and names diverge — reject or score worse.
        if watch_score is None:
            self.assertIsNotNone(pencil_score)
        else:
            self.assertGreater(pencil_score, watch_score)

    def test_shared_tracking_assigns_correct_payouts(self) -> None:
        """Bug case: two orders share TN; pencils must not get watch totals."""
        lots = _usabg_lots_from_receipts(
            [
                _row(name=WATCH_NAME, qty=4, unit="189.99", sku="MEH34LW/A"),
                _row(name=PENCIL_NAME_USABG, qty=4, unit="96.00", sku="MX2D3AM/A"),
            ]
        )
        lots_by_tn = {TN.casefold(): lots}
        lots_by_id = {lot["lot_id"]: lot for lot in lots}
        meta_by_lot = {lid: _lot_as_match_meta(lot) for lid, lot in lots_by_id.items()}

        watch_3 = _item(item_id=1, qty=3, description=WATCH_NAME, price_sold="189.99")
        watch_1 = _item(item_id=2, qty=1, description=WATCH_NAME, price_sold="189.99")
        pencil_3 = _item(
            item_id=3, qty=3, description=PENCIL_NAME_LOCAL, price_sold="97.00"
        )
        pencil_1 = _item(
            item_id=4, qty=1, description=PENCIL_NAME_LOCAL, price_sold="97.00"
        )
        trackings = [TN]
        claimants = [
            (watch_3, trackings),
            (watch_1, trackings),
            (pencil_3, trackings),
            (pencil_1, trackings),
        ]

        assignment = _assign_items_to_lots(
            claimants=claimants, lots_by_tn=lots_by_tn
        )
        self.assertEqual(len(assignment), 4)

        watch_lot_ids = {
            lot["lot_id"] for lot in lots if lot["sku"] == "MEH34LW/A"
        }
        pencil_lot_ids = {
            lot["lot_id"] for lot in lots if lot["sku"] == "MX2D3AM/A"
        }
        self.assertIn(assignment[1], watch_lot_ids)
        self.assertIn(assignment[2], watch_lot_ids)
        self.assertIn(assignment[3], pencil_lot_ids)
        self.assertIn(assignment[4], pencil_lot_ids)

        supply_by_lot = {lid: int(lot["qty"]) for lid, lot in lots_by_id.items()}
        item_demands = [
            (item.id, item.quantity, [assignment[item.id]])
            for item, _tns in claimants
        ]
        allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
            item_demands=item_demands,
            supply_by_tn=supply_by_lot,
        )
        self.assertEqual(allocated[1], 3)
        self.assertEqual(allocated[2], 1)
        self.assertEqual(allocated[3], 3)
        self.assertEqual(allocated[4], 1)

        order_watch = _order(order_id=10, store_order_number="113-9699974-6146638")
        order_pencil = _order(order_id=11, store_order_number="113-1268739-3882619")
        pairs = [
            (watch_3, order_watch),
            (watch_1, order_watch),
            (pencil_3, order_pencil),
            (pencil_1, order_pencil),
        ]
        matches = []
        for item, order in pairs:
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
        self.assertEqual(by_item[1].received_price, Decimal("189.99"))
        self.assertEqual(by_item[1].received_ext_total, Decimal("569.97"))
        self.assertFalse(by_item[1].amount_discrepancy)

        self.assertEqual(by_item[2].received_price, Decimal("189.99"))
        self.assertEqual(by_item[2].received_ext_total, Decimal("189.99"))
        self.assertFalse(by_item[2].amount_discrepancy)

        # Local expected $97 vs USABG $96 — still flags, but correct payout.
        self.assertEqual(by_item[3].received_price, Decimal("96.00"))
        self.assertEqual(by_item[3].received_ext_total, Decimal("288.00"))
        self.assertEqual(by_item[3].expected_amount, Decimal("291.00"))
        self.assertTrue(by_item[3].amount_discrepancy)

        self.assertEqual(by_item[4].received_price, Decimal("96.00"))
        self.assertEqual(by_item[4].received_ext_total, Decimal("96.00"))
        self.assertEqual(by_item[4].expected_amount, Decimal("97.00"))
        self.assertTrue(by_item[4].amount_discrepancy)

        for m in matches:
            self.assertEqual(m.tracking_number, TN)

    def test_zero_payout_does_not_inherit_single_lot(self) -> None:
        """E-delivery / $0 lines share TN with a laptop box but are not on the PO."""
        laptop_name = (
            'HP OmniBook 5 16" Touchscreen Laptop - Intel Core Ultra 5 225U '
            "- 16GB RAM - 1TB SSD - Win11 Home"
        )
        lots = _usabg_lots_from_receipts(
            [_row(name=laptop_name, qty=2, unit="515.00", sku="16-af1055cl")]
        )
        self.assertEqual(len(lots), 1)
        lots_by_tn = {TN.casefold(): lots}
        lots_by_id = {lot["lot_id"]: lot for lot in lots}
        meta_by_lot = {lid: _lot_as_match_meta(lot) for lid, lot in lots_by_id.items()}

        laptop = _item(item_id=1, qty=2, description=laptop_name, price_sold="515.00")
        e_delivery = _item(
            item_id=2,
            qty=2,
            description="Microsoft 365 Personal Subscription (e-delivery)",
            price_sold="0",
        )
        self.assertTrue(_is_zero_payout(e_delivery))
        self.assertFalse(_is_zero_payout(laptop))

        trackings = [TN]
        # Production filters $0 before lot assignment / allocation.
        claimants = [
            (item, trackings)
            for item in (laptop, e_delivery)
            if not _is_zero_payout(item)
        ]
        self.assertEqual([c[0].id for c in claimants], [1])

        assignment = _assign_items_to_lots(
            claimants=claimants, lots_by_tn=lots_by_tn
        )
        self.assertEqual(assignment, {1: lots[0]["lot_id"]})
        self.assertNotIn(2, assignment)

        # Without the filter, single-lot would force e-delivery onto the laptop lot.
        forced = _assign_items_to_lots(
            claimants=[(laptop, trackings), (e_delivery, trackings)],
            lots_by_tn=lots_by_tn,
        )
        self.assertEqual(forced.get(2), lots[0]["lot_id"])

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
        self.assertNotIn(2, allocated)

        order = _order(order_id=20, store_order_number="111-0000000-0000000")
        matches = _matches_for_tn_allocations(
            item=laptop,
            order=order,
            trackings=trackings,
            tn_allocations=allocated_by_lot.get(laptop.id, []),
            meta_by_tn=meta_by_lot,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].received_ext_total, Decimal("1030.00"))
        self.assertEqual(matches[0].expected_amount, Decimal("1030.00"))
        self.assertFalse(matches[0].amount_discrepancy)

    def test_item_owned_tracking_does_not_claim_sibling_package(self) -> None:
        """Submitted item on TN2 must not match a warehouse lot on sibling TN1.

        Mirrors multi-SKU orders where a scanned sibling already owns TN1 and a
        submitted sibling only owns TN2 — item-owned trackings only, no order merge.
        """
        tn1 = "TBA335391801005"
        tn2 = "TBA335478885927"
        rye_name = (
            "Google - Fitbit Air - Special Edition Stephen Curry - Screenless "
            "Fitness and Health Tracking - Works with Android and iOS - Rye"
        )
        obsidian_name = "Google Fitbit Air - Screenless Activity Tracker, Obsidian"

        lots = _usabg_lots_from_receipts(
            [
                _row(
                    name=rye_name,
                    qty=6,
                    unit="129.99",
                    sku="GA11551-US",
                    tn=tn1,
                    po="2150582",
                )
            ]
        )
        self.assertEqual(len(lots), 1)
        lots_by_tn = {tn1.casefold(): lots}

        rye = _item(item_id=1, qty=6, description=rye_name, price_sold="129.99")
        obsidian = _item(
            item_id=2, qty=3, description=obsidian_name, price_sold="99.99"
        )

        # Production: each item only claims its own shipment tracking(s).
        assignment = _assign_items_to_lots(
            claimants=[
                (rye, [tn1]),
                (obsidian, [tn2]),
            ],
            lots_by_tn=lots_by_tn,
        )
        self.assertEqual(assignment.get(1), lots[0]["lot_id"])
        self.assertNotIn(2, assignment)

        # Pre-fix bug: order-level merge gave Obsidian TN1 as well → false match.
        buggy = _assign_items_to_lots(
            claimants=[
                (rye, [tn1]),
                (obsidian, [tn2, tn1]),
            ],
            lots_by_tn=lots_by_tn,
        )
        self.assertEqual(buggy.get(2), lots[0]["lot_id"])


if __name__ == "__main__":
    unittest.main()
