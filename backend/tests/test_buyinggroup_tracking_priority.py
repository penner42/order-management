"""Shipment identifiers take precedence over Walmart's order-wide fallback."""
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.models import Item, Shipment, ShipmentItem
from app.models.item import ItemStatus
from app.utils.buying_group_apis import buyinggroup as api, check_receiving as receiving
from tests import test_buyinggroup_rewards as reward_tests


class TrackingPriorityTests(unittest.TestCase):
    setUp = reward_tests.BuyingGroupRewardTests.setUp

    def configure_order(self):
        self.item.order.store.name = 'Walmart'
        self.item.order.store_order_number = '200015275735103'
        self.item.shipment_items[0].shipment.tracking_number = '543161235300'
        self.item.quantity = 2
        self.item.price_sold = Decimal('199.00')
        self.item.description = 'HP 14-inch HD Windows 11 Laptop, Intel N150, 4GB RAM, 128GB Storage, Tranquil Pink, 14-ep2111wm'
        self.db.commit()

    def check(self, *, shipment_product='Hp-14-Laptop-N150-4Gb-128Gb-9179',
              shipment_upc='199764359179', alternate=False):
        number = self.item.order.store_order_number
        receipts = [api._parse_receipt_row({
            'key': 'pink', 'receipt_id': 'R-PINK', 'tracking_id': 'WAREHOUSE' if alternate else '543161235300',
            'alt_tracking': '543161235300' if alternate else None, 'quantity': 2, 'total': '398.00',
        }), api._parse_receipt_row({
            'key': 'blue', 'receipt_id': 'R-BLUE', 'tracking_id': number, 'quantity': 1, 'total': '199.00',
        })]
        def details(**kwargs):
            pink = kwargs['receipt_key'] == 'pink'
            return {'receipt_id': 'R-PINK' if pink else 'R-BLUE', 'received_items': [{
                'verified': True, 'qty': 2 if pink else 1, 'price': '199.00',
                'total': '398.00' if pink else '199.00',
                'item_id': shipment_product if pink else 'Hp-14-Laptop-N150-4Gb-128Gb-Blue-9186',
                'upc': shipment_upc if pink else '199764359186',
            }]}
        with (patch.object(api, 'list_receipts', return_value=receipts),
              patch.object(api, 'get_receipt_details', side_effect=details),
              patch.object(api, 'list_tracking_rewards', return_value={})):
            return receiving.check_receiving_for_group(self.db, buying_group_id=self.group.id)

    def test_fedex_match_wins_over_similar_blue_product_on_order_receipt(self):
        self.configure_order()
        # Reproduce the imported order, including its already-scanned blue line.
        blue = Item(order=self.item.order, quantity=1, price_sold=199,
                    description='HP 14 inch HD Windows Laptop Intel Processor N150 4GB 128GB UFS Waterfall Blue',
                    status=ItemStatus.SCANNED, receipt_id='R-BLUE',
                    receipt_tracking_number=self.item.order.store_order_number,
                    receipt_upc='199764359186', shipment_items=[ShipmentItem(
                        shipment=Shipment(tracking_number=self.item.order.store_order_number))])
        self.db.add(blue)
        self.db.commit()
        result = self.check()
        pink_matches = [m for m in result.matches if m.item_id == self.item.id]
        self.assertEqual([(m.receipt_id, m.received_qty) for m in pink_matches], [('R-PINK', 2)])
        self.assertFalse(any(row['receipt_id'] == 'R-PINK' for row in result.session_payload['unknown_receipts']))

    def test_alternate_shipment_tracking_also_takes_priority(self):
        self.configure_order()
        result = self.check(alternate=True)
        self.assertEqual([(m.receipt_id, m.received_qty) for m in result.matches], [('R-PINK', 2)])

    def test_ineligible_shipment_product_still_allows_order_fallback(self):
        self.configure_order()
        self.item.receipt_upc = '199764359186'
        self.item.quantity = 1
        self.db.commit()
        result = self.check()
        self.assertEqual([(m.receipt_id, m.received_qty) for m in result.matches], [('R-BLUE', 1)])

    def test_applied_order_receipt_remains_reserved_for_scanned_item(self):
        self.configure_order()
        self.item.quantity = 1
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = 'R-BLUE'
        self.item.receipt_tracking_number = self.item.order.store_order_number
        self.db.commit()
        result = self.check()
        self.assertTrue(all(m.update_receipt_only for m in result.matches))
        self.assertTrue(all(m.receipt_id == 'R-BLUE' for m in result.matches))
        pink, = [r for r in result.session_payload['unknown_receipts'] if r['receipt_id'] == 'R-PINK']
        self.assertEqual(pink['quantity'], 2)
