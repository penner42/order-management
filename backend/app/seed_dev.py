"""Seed development sample data. Idempotent: skips if sample data already present."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import settings
from app.models import (
    User,
    Store,
    StoreAccount,
    BuyingGroup,
    Reward,
    PaymentMethod,
    Order,
    OrderPaymentMethod,
    Item,
    Shipment,
    ShipmentItem,
    Payment,
    PaymentLineItem,
    Portal,
)
from app.models.item import ItemStatus


# Marker store name to detect if we already ran seed (idempotency)
_SEED_MARKER_STORE_NAME = "Dev Seed Amazon"

# Sample data user (owns all seeded orders, stores, etc.)
_SEED_USERNAME = "orders"
_SEED_PASSWORD = "orders"
_SEED_USER_NAME = "Orders"


def seed_dev_data(db: Session) -> dict:
    """
    Create sample data for development. Creates a user "orders"/"orders" and assigns all
    sample data to that user. If a store named _SEED_MARKER_STORE_NAME already exists,
    does nothing and returns skipped info.
    Returns a dict with "skipped" (bool) and "message" (str).
    """
    admin = db.query(User).filter(User.username == settings.admin_username).first()
    if not admin:
        return {"skipped": True, "message": "Admin user not found; run app once to bootstrap."}

    existing = db.query(Store).filter(Store.name == _SEED_MARKER_STORE_NAME).first()
    if existing:
        return {"skipped": True, "message": "Sample data already loaded."}

    # Create or get the sample data user (role "user" so they own orders)
    orders_user = db.query(User).filter(User.username == _SEED_USERNAME).first()
    if not orders_user:
        orders_user = User(
            username=_SEED_USERNAME,
            name=_SEED_USER_NAME,
            role="user",
            hashed_password=hash_password(_SEED_PASSWORD),
        )
        db.add(orders_user)
        db.flush()
    user_id = orders_user.id

    # Create in dependency order
    # 1. Stores and store accounts
    store_amazon = Store(user_id=user_id, name=_SEED_MARKER_STORE_NAME)
    db.add(store_amazon)
    db.flush()
    account_amazon = StoreAccount(store_id=store_amazon.id, name="Personal")
    db.add(account_amazon)
    db.flush()

    store_target = Store(user_id=user_id, name="Target")
    db.add(store_target)
    db.flush()
    account_target = StoreAccount(store_id=store_target.id, name="Personal")
    db.add(account_target)
    db.flush()

    store_walmart = Store(user_id=user_id, name="Walmart")
    db.add(store_walmart)
    db.flush()
    account_walmart = StoreAccount(store_id=store_walmart.id, name="Business")
    db.add(account_walmart)
    db.flush()

    # 2. Buying groups, rewards, payment methods
    bg_family = BuyingGroup(user_id=user_id, name="Family")
    bg_office = BuyingGroup(user_id=user_id, name="Office")
    db.add_all([bg_family, bg_office])
    db.flush()

    reward_prime = Reward(user_id=user_id, name="Amazon Prime")
    reward_redcard = Reward(user_id=user_id, name="Target RedCard")
    db.add_all([reward_prime, reward_redcard])
    db.flush()

    pm_visa = PaymentMethod(user_id=user_id, label="Visa ****1234", reward_id=reward_prime.id)
    pm_paypal = PaymentMethod(user_id=user_id, label="PayPal")
    pm_amex = PaymentMethod(user_id=user_id, label="Amex ****5678", reward_id=reward_redcard.id)
    db.add_all([pm_visa, pm_paypal, pm_amex])
    db.flush()

    # 3. Orders
    base_ts = datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
    order1 = Order(
        user_id=user_id,
        store_id=store_amazon.id,
        store_account_id=account_amazon.id,
        buying_group_id=bg_family.id,
        store_order_number="AMZ-112-9876543",
        status="active",
        purchase_date=base_ts,
        shipping=Decimal("5.99"),
        sales_tax=Decimal("12.40"),
        notes="Gift wrap requested",
    )
    order2 = Order(
        user_id=user_id,
        store_id=store_amazon.id,
        store_account_id=account_amazon.id,
        buying_group_id=bg_family.id,
        store_order_number="AMZ-113-1112233",
        status="active",
        purchase_date=datetime(2025, 1, 20, 9, 0, 0, tzinfo=timezone.utc),
        shipping=Decimal("0.00"),
        sales_tax=Decimal("8.20"),
    )
    order3 = Order(
        user_id=user_id,
        store_id=store_target.id,
        store_account_id=account_target.id,
        buying_group_id=bg_office.id,
        store_order_number="TGT-445566",
        status="active",
        purchase_date=datetime(2025, 1, 22, 16, 45, 0, tzinfo=timezone.utc),
        shipping=Decimal("0.00"),
        sales_tax=Decimal("15.00"),
    )
    order4 = Order(
        user_id=user_id,
        store_id=store_walmart.id,
        store_account_id=account_walmart.id,
        buying_group_id=None,
        store_order_number="WMT-778899",
        status="active",
        purchase_date=datetime(2025, 1, 25, 11, 0, 0, tzinfo=timezone.utc),
        shipping=Decimal("7.99"),
        sales_tax=Decimal("0.00"),
    )
    db.add_all([order1, order2, order3, order4])
    db.flush()

    # 4. Order payment methods
    db.add(OrderPaymentMethod(order_id=order1.id, payment_method_id=pm_visa.id, amount=Decimal("89.99")))
    db.add(OrderPaymentMethod(order_id=order1.id, payment_method_id=pm_paypal.id, amount=Decimal("18.40")))
    db.add(OrderPaymentMethod(order_id=order2.id, payment_method_id=pm_visa.id, amount=Decimal("52.20")))
    db.add(OrderPaymentMethod(order_id=order3.id, payment_method_id=pm_amex.id, amount=Decimal("115.00")))
    db.add(OrderPaymentMethod(order_id=order4.id, payment_method_id=pm_visa.id))
    db.flush()

    # 5. Items
    item1_1 = Item(
        order_id=order1.id,
        price_paid=Decimal("29.99"),
        price_sold=Decimal("35.00"),
        status=ItemStatus.SCANNED,
        quantity=1,
        description="Wireless earbuds",
    )
    item1_2 = Item(
        order_id=order1.id,
        price_paid=Decimal("59.99"),
        price_sold=Decimal("72.00"),
        status=ItemStatus.PAYMENT_RECEIVED,
        quantity=1,
        description="Tablet stand",
    )
    item2_1 = Item(
        order_id=order2.id,
        price_paid=Decimal("52.20"),
        price_sold=None,
        status=ItemStatus.SHIPPED,
        quantity=1,
        description="Desk lamp",
    )
    item3_1 = Item(
        order_id=order3.id,
        price_paid=Decimal("45.00"),
        price_sold=Decimal("50.00"),
        status=ItemStatus.SCANNED,
        quantity=2,
        description="Office supplies pack",
    )
    item3_2 = Item(
        order_id=order3.id,
        price_paid=Decimal("25.00"),
        price_sold=None,
        status=ItemStatus.PURCHASED,
        quantity=1,
        description="Sticky notes bulk",
    )
    item4_1 = Item(
        order_id=order4.id,
        price_paid=Decimal("19.99"),
        price_sold=None,
        status=ItemStatus.PURCHASED,
        quantity=1,
        description="Kitchen gadget",
    )
    db.add_all([item1_1, item1_2, item2_1, item3_1, item3_2, item4_1])
    db.flush()

    # 6. Shipments and shipment items
    ship1 = Shipment(
        user_id=user_id,
        tracking_number="1Z999AA10123456784",
        shipped_at=datetime(2025, 1, 16, 10, 0, 0, tzinfo=timezone.utc),
        notes="Amazon fulfillment",
    )
    db.add(ship1)
    db.flush()
    db.add(ShipmentItem(shipment_id=ship1.id, item_id=item1_1.id))
    db.add(ShipmentItem(shipment_id=ship1.id, item_id=item1_2.id))

    ship2 = Shipment(
        user_id=user_id,
        tracking_number="TGT9876543210",
        shipped_at=datetime(2025, 1, 23, 8, 0, 0, tzinfo=timezone.utc),
    )
    db.add(ship2)
    db.flush()
    db.add(ShipmentItem(shipment_id=ship2.id, item_id=item3_1.id))

    # 7. Payments and payment line items (buying group payments for items)
    pay1 = Payment(buying_group_id=bg_family.id, payment_id="PAY-FAM-001")
    db.add(pay1)
    db.flush()
    db.add(PaymentLineItem(payment_id=pay1.id, item_id=item1_1.id))
    db.add(PaymentLineItem(payment_id=pay1.id, item_id=item1_2.id))

    pay2 = Payment(buying_group_id=bg_office.id, payment_id="PAY-OFF-001")
    db.add(pay2)
    db.flush()
    db.add(PaymentLineItem(payment_id=pay2.id, item_id=item3_1.id))

    # 8. Portals
    db.add_all([
        Portal(name="Main portal"),
        Portal(name="Partner portal"),
        Portal(name="Test portal"),
    ])

    db.commit()
    return {"skipped": False, "message": "Sample data loaded successfully."}
