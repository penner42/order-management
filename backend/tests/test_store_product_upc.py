"""Store UPC persistence, reimports and splits use an isolated database."""
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order
from app.models.item import ItemStatus
from app.bfmr_orders import capture_import_metadata
from app.routers.store_imports import _apply_items_and_shipments, _build_order_diff
from app.schemas.item import ItemRead, ItemSplitRequest
from app.utils.item_split import split_item_record
from app.utils.product_upc import product_upc


class StoreProductUpcTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.order = Order(store_id=1, store_order_number='O1')
        self.db.add(self.order)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def payload(self, upc='000012345678'):
        return {'items': [{'name': 'Product', 'externalSku': '1234567', 'logicalItemId': 'L1',
                           'upc': upc, 'quantities': {'ordered': 2}, 'pricing': {'unitPrice': 10}}]}

    def test_create_persist_serialize_and_split(self):
        payload = self.payload()
        _apply_items_and_shipments(self.db, payload, 'costco', self.order,
                                  external_order_id='O1', existing_order=False)
        capture_import_metadata(self.db, self.order, payload, 'costco')
        self.db.commit()
        self.db.expire_all()
        item = self.db.query(Item).one()
        self.assertEqual(item.upc, '000012345678')
        self.assertIsNone(item.receipt_upc)
        self.assertEqual(ItemRead.model_validate(item).upc, '000012345678')
        split = split_item_record(self.db, item, ItemSplitRequest(keep_quantity=1))
        self.db.commit()
        self.assertEqual(split.upc, item.upc)

    def test_backfill_locked_item_preserves_receipt_and_missing_or_conflicting_data(self):
        item = Item(order_id=self.order.id, description='Product', quantity=2,
                    status=ItemStatus.SHIPPED, receipt_upc='123456789012')
        self.db.add(item)
        self.db.flush()
        capture_import_metadata(self.db, self.order, self.payload(), 'costco')
        self.assertEqual(item.upc, '000012345678')
        for value in [None, '', 'invalid', '999999999999']:
            capture_import_metadata(self.db, self.order, self.payload(value), 'costco')
            self.assertEqual(item.upc, '000012345678')
        self.assertEqual(item.receipt_upc, '123456789012')
        self.assertEqual(item.status, ItemStatus.SHIPPED)

    def test_duplicate_names_with_distinct_products_only_backfill_matching_line(self):
        item = Item(order_id=self.order.id, description='Product', import_line_key='L1', external_sku='1234567')
        self.db.add(item)
        self.db.flush()
        payload = self.payload()
        payload['items'].append({**payload['items'][0], 'logicalItemId': 'L2',
                                 'externalSku': 'OTHER', 'upc': '123456789012'})
        capture_import_metadata(self.db, self.order, payload, 'costco')
        self.assertEqual(item.upc, '000012345678')
        item.upc = None
        item.import_line_key = None
        item.external_sku = None
        capture_import_metadata(self.db, self.order, payload, 'costco')
        self.assertIsNone(item.upc)

    def test_validation(self):
        self.assertEqual(product_upc(' 000012345678 '), '000012345678')
        for value in [None, True, 123456789012, '123', 'a' * 12, '1' * 65]:
            self.assertIsNone(product_upc(value))

    def test_upc_only_backfill_is_visible_in_diff(self):
        item = Item(order_id=self.order.id, description='Product', quantity=2, price_paid=10)
        self.db.add(item)
        self.db.flush()
        diff = _build_order_diff(self.db, self.order, self.payload())
        self.assertTrue(diff['has_changes'])
        self.assertEqual(diff['items']['matched'][0]['changes'], ['upc'])
        capture_import_metadata(self.db, self.order, self.payload(), 'costco')
        self.assertFalse(_build_order_diff(self.db, self.order, self.payload())['has_changes'])
