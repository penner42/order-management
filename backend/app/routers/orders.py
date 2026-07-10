"""Orders API."""
from datetime import date, datetime, timezone
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload
from app.auth import get_current_user
from app.database import get_db
from app.models import (
    User,
    Order,
    Item,
    OrderPaymentMethod,
    Payment,
    PaymentLineItem,
    Store,
    BuyingGroup,
    PaymentMethod,
    Shipment,
    ShipmentItem,
)
from app.models.user import get_default_app_user_id
from app.schemas.order import OrderRead, OrderCreate, OrderUpdate, OrderPaymentMethodCreate, OrderListPage
from app.schemas.item import ItemCreateNested

router = APIRouter(prefix="/orders", tags=["orders"])


def _like_escape(s: str) -> str:
    """Escape % and _ for safe use in LIKE."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _effective_item_status(item: Item) -> str:
    """Item status for display/filter: payment dates (from Payment) override when item is on a payment."""
    if item.payment_received_at is not None:
        return "payment_received"
    if item.payment_sent_at is not None:
        return "payment_sent"
    if item.payment_requested_at is not None:
        return "payment_requested"
    return item.status.value


def _build_orders_query(
    *,
    db: Session,
    order_status: str | None,
    status: list[str],
    buying_group_id: list[int],
    store_id: list[int],
    store_account_id: list[int],
    date_from: str | None,
    date_to: str | None,
    date_from_utc: str | None,
    date_to_utc: str | None,
    search: str | None,
):
    # Order-level filters: which orders to include
    q = db.query(Order).order_by(Order.purchase_date.desc())
    if order_status:
        q = q.filter(Order.status == order_status)
    if status:
        # Orders that have at least one item with one of these statuses (item or payment), or orders with no items.
        base = db.query(Order)
        if order_status:
            base = base.filter(Order.status == order_status)
        item_statuses = [s for s in status if s not in ("payment_requested", "payment_sent", "payment_received")]
        payment_statuses = [s for s in status if s in ("payment_requested", "payment_sent", "payment_received")]
        queries = []
        if item_statuses:
            queries.append(base.join(Item).filter(Item.status.in_(item_statuses)).distinct().with_entities(Order.id))
        if payment_statuses:
            # Orders that have an item on a payment with the given payment state (requested/sent/received)
            payment_filters = []
            if "payment_requested" in payment_statuses:
                payment_filters.append(Payment.payment_requested_at.isnot(None))
            if "payment_sent" in payment_statuses:
                payment_filters.append(Payment.payment_sent_at.isnot(None))
            if "payment_received" in payment_statuses:
                payment_filters.append(Payment.payment_received_at.isnot(None))
            if payment_filters:
                queries.append(
                    base.join(Item)
                    .join(PaymentLineItem, PaymentLineItem.item_id == Item.id)
                    .join(Payment, Payment.id == PaymentLineItem.payment_id)
                    .filter(or_(*payment_filters))
                    .distinct()
                    .with_entities(Order.id)
                )
        ids_with_matching_items = queries[0] if len(queries) == 1 else queries[0].union(*queries[1:])
        ids_with_no_items = base.outerjoin(Item).filter(Item.id.is_(None)).with_entities(Order.id)
        order_ids = ids_with_matching_items.union(ids_with_no_items).subquery()
        q = db.query(Order).filter(Order.id.in_(order_ids)).order_by(Order.purchase_date.desc())
        if order_status:
            q = q.filter(Order.status == order_status)
    if buying_group_id:
        q = q.filter(Order.buying_group_id.in_(buying_group_id))
    if store_id or store_account_id:
        clauses = []
        if store_id:
            clauses.append(Order.store_id.in_(store_id))
        if store_account_id:
            clauses.append(Order.store_account_id.in_(store_account_id))
        q = q.filter(or_(*clauses))
    # Prefer UTC range (from frontend local-date selection) for correct comparison with DB UTC timestamps
    if date_from_utc is not None or date_to_utc is not None:
        try:
            if date_from_utc:
                dt_from = datetime.fromisoformat(date_from_utc.replace("Z", "+00:00"))
                if dt_from.tzinfo is None:
                    dt_from = dt_from.replace(tzinfo=timezone.utc)
                q = q.filter(Order.purchase_date >= dt_from)
            if date_to_utc:
                dt_to = datetime.fromisoformat(date_to_utc.replace("Z", "+00:00"))
                if dt_to.tzinfo is None:
                    dt_to = dt_to.replace(tzinfo=timezone.utc)
                q = q.filter(Order.purchase_date <= dt_to)
        except (ValueError, TypeError):
            pass
    else:
        if date_from:
            try:
                d = date.fromisoformat(date_from)
                q = q.filter(func.date(Order.purchase_date) >= d)
            except ValueError:
                pass
        if date_to:
            try:
                d = date.fromisoformat(date_to)
                q = q.filter(func.date(Order.purchase_date) <= d)
            except ValueError:
                pass

    search_order_level_ids = None
    search_item_match_pairs = None
    if search and search.strip():
        term = f"%{_like_escape(search.strip())}%"
        escape = "\\"
        base_order = db.query(Order)
        if order_status:
            base_order = base_order.filter(Order.status == order_status)
        # Order-level matches: show whole order
        by_order_number = (
            base_order.filter(Order.store_order_number.isnot(None))
            .filter(func.lower(Order.store_order_number).like(func.lower(term), escape=escape))
            .with_entities(Order.id)
            .distinct()
        )
        by_store = base_order.join(Store).filter(func.lower(Store.name).like(func.lower(term), escape=escape)).with_entities(Order.id).distinct()
        by_buying_group = (
            base_order.join(BuyingGroup)
            .filter(BuyingGroup.name.isnot(None))
            .filter(func.lower(BuyingGroup.name).like(func.lower(term), escape=escape))
            .with_entities(Order.id)
            .distinct()
        )
        by_payment_label = (
            base_order.join(OrderPaymentMethod)
            .join(PaymentMethod)
            .filter(func.lower(PaymentMethod.label).like(func.lower(term), escape=escape))
            .with_entities(Order.id)
            .distinct()
        )
        by_dollars = base_order.filter(
            or_(
                cast(Order.shipping, String).like(term, escape=escape),
                cast(Order.sales_tax, String).like(term, escape=escape),
            )
        ).with_entities(Order.id).distinct()
        by_payment_amount = (
            base_order.join(OrderPaymentMethod)
            .filter(cast(OrderPaymentMethod.amount, String).like(term, escape=escape))
            .with_entities(Order.id)
            .distinct()
        )
        order_level_ids_subq = by_order_number.union(by_store, by_buying_group, by_payment_label, by_dollars, by_payment_amount)
        search_order_level_ids = set(row[0] for row in order_level_ids_subq.all())

        # Item-level matches: (order_id, item_id) for filtering items when order is not in order_level
        by_item_desc_pairs = (
            base_order.join(Item)
            .filter(Item.description.isnot(None))
            .filter(func.lower(Item.description).like(func.lower(term), escape=escape))
            .with_entities(Order.id, Item.id)
            .distinct()
        )
        by_item_dollars_pairs = (
            base_order.join(Item)
            .filter(
                or_(
                    cast(Item.price_paid, String).like(term, escape=escape),
                    cast(Item.price_sold, String).like(term, escape=escape),
                    cast(Item.shipping, String).like(term, escape=escape),
                    cast(Item.sales_tax, String).like(term, escape=escape),
                )
            )
            .with_entities(Order.id, Item.id)
            .distinct()
        )
        # Tracking number is per-shipment: show only items in the matching shipment(s)
        by_tracking_pairs = (
            base_order.join(Item)
            .join(ShipmentItem)
            .join(Shipment)
            .filter(Shipment.tracking_number.isnot(None))
            .filter(func.lower(Shipment.tracking_number).like(func.lower(term), escape=escape))
            .with_entities(Order.id, Item.id)
            .distinct()
        )
        search_item_match_pairs = set(by_item_desc_pairs.union(by_item_dollars_pairs).union(by_tracking_pairs).all())

        # Orders to include: matched at order level OR at item/tracking level
        by_item_desc = (
            base_order.join(Item)
            .filter(Item.description.isnot(None))
            .filter(func.lower(Item.description).like(func.lower(term), escape=escape))
            .with_entities(Order.id)
            .distinct()
        )
        by_item_dollars = base_order.join(Item).filter(
            or_(
                cast(Item.price_paid, String).like(term, escape=escape),
                cast(Item.price_sold, String).like(term, escape=escape),
                cast(Item.shipping, String).like(term, escape=escape),
                cast(Item.sales_tax, String).like(term, escape=escape),
            )
        ).with_entities(Order.id).distinct()
        by_tracking = (
            base_order.join(Item)
            .join(ShipmentItem)
            .join(Shipment)
            .filter(Shipment.tracking_number.isnot(None))
            .filter(func.lower(Shipment.tracking_number).like(func.lower(term), escape=escape))
            .with_entities(Order.id)
            .distinct()
        )
        search_ids = order_level_ids_subq.union(by_item_desc, by_item_dollars, by_tracking).subquery()
        q = q.filter(Order.id.in_(search_ids))

    return q, search_order_level_ids, search_item_match_pairs


def _materialize_orders(
    *,
    q,
    status: list[str],
    search_order_level_ids,
    search_item_match_pairs,
):
    # Eager load relationships to avoid N+1 when building OrderRead (store, store_account, buying_group,
    # items + payment dates from Payment, order_payments + payment_method). Use selectinload for one-to-many to avoid cartesian product.
    q = q.options(
        joinedload(Order.store),
        joinedload(Order.store_account),
        joinedload(Order.buying_group),
        selectinload(Order.items).selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment),
        selectinload(Order.order_payments).joinedload(OrderPaymentMethod.payment_method),
    )
    orders = q.all()

    # Build response: apply status filter and, when search is item-level-only, show only matching items
    status_set = set(status) if status else None
    if status_set is not None or search_order_level_ids is not None:
        result = []
        for o in orders:
            read = OrderRead.model_validate(o)
            items = read.items
            if status_set is not None:
                # Match by effective status (item status or payment status when on a payment)
                items = [read.items[i] for i in range(len(read.items)) if _effective_item_status(o.items[i]) in status_set]
            if search_order_level_ids is not None:
                if o.id in search_order_level_ids:
                    pass  # whole order: items already filtered by status if any
                else:
                    # matched only by item: show only items that matched search
                    items = [i for i in items if (o.id, i.id) in search_item_match_pairs]
            result.append(read.model_copy(update={"items": items}))
        return result

    return [OrderRead.model_validate(o) for o in orders]


@router.get("", response_model=list[OrderRead])
def list_orders(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    order_status: str | None = Query(default=None, alias="order_status"),  # when "imported", only imported; else exclude imported
    status: list[str] = Query(default=[], alias="status"),
    buying_group_id: list[int] = Query(default=[], alias="buying_group_id"),
    store_id: list[int] = Query(default=[], alias="store_id"),
    store_account_id: list[int] = Query(default=[], alias="store_account_id"),
    date_from: str | None = None,
    date_to: str | None = None,
    date_from_utc: str | None = None,
    date_to_utc: str | None = None,
    search: str | None = Query(default=None, alias="q"),
):
    q, search_order_level_ids, search_item_match_pairs = _build_orders_query(
        db=db,
        order_status=order_status,
        status=status,
        buying_group_id=buying_group_id,
        store_id=store_id,
        store_account_id=store_account_id,
        date_from=date_from,
        date_to=date_to,
        date_from_utc=date_from_utc,
        date_to_utc=date_to_utc,
        search=search,
    )
    return _materialize_orders(
        q=q,
        status=status,
        search_order_level_ids=search_order_level_ids,
        search_item_match_pairs=search_item_match_pairs,
    )


@router.get("/paged", response_model=OrderListPage)
def list_orders_paged(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    order_status: str | None = Query(default=None, alias="order_status"),  # when "imported", only imported; else exclude imported
    status: list[str] = Query(default=[], alias="status"),
    buying_group_id: list[int] = Query(default=[], alias="buying_group_id"),
    store_id: list[int] = Query(default=[], alias="store_id"),
    store_account_id: list[int] = Query(default=[], alias="store_account_id"),
    date_from: str | None = None,
    date_to: str | None = None,
    date_from_utc: str | None = None,
    date_to_utc: str | None = None,
    search: str | None = Query(default=None, alias="q"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50),
):
    allowed = {0, 25, 50, 100}
    if per_page not in allowed:
        per_page = 50

    q, search_order_level_ids, search_item_match_pairs = _build_orders_query(
        db=db,
        order_status=order_status,
        status=status,
        buying_group_id=buying_group_id,
        store_id=store_id,
        store_account_id=store_account_id,
        date_from=date_from,
        date_to=date_to,
        date_from_utc=date_from_utc,
        date_to_utc=date_to_utc,
        search=search,
    )

    total = int(q.order_by(None).with_entities(func.count(func.distinct(Order.id))).scalar() or 0)
    if per_page == 0:
        pages = 1
        page = 1
    else:
        pages = max(1, int(math.ceil(total / per_page)))
        page = min(max(1, page), pages)
        q = q.offset((page - 1) * per_page).limit(per_page)

    items = _materialize_orders(
        q=q,
        status=status,
        search_order_level_ids=search_order_level_ids,
        search_item_match_pairs=search_item_match_pairs,
    )
    return OrderListPage(items=items, page=page, per_page=per_page, total=total, pages=pages)


@router.post("", response_model=OrderRead)
def create_order(data: OrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = data.user_id or (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    order = Order(
        notes=data.notes,
        purchase_date=data.purchase_date,
        user_id=user_id,
        store_id=data.store_id,
        store_account_id=data.store_account_id,
        buying_group_id=data.buying_group_id,
        store_order_number=data.store_order_number,
        order_discount=data.order_discount,
        insurance_cost=data.insurance_cost,
        status=getattr(data, "status", "active") or "active",
    )
    db.add(order)
    db.flush()
    if data.payment_methods:
        seen = set()
        for pm in data.payment_methods:
            if pm.payment_method_id in seen:
                raise HTTPException(
                    status_code=400,
                    detail="Each payment method can only be used once per order.",
                )
            seen.add(pm.payment_method_id)
    for pm in data.payment_methods or []:
        opm = OrderPaymentMethod(
            order_id=order.id,
            payment_method_id=pm.payment_method_id,
            amount=pm.amount,
        )
        db.add(opm)
    for it in data.items or []:
        item = Item(
            order_id=order.id,
            **it.model_dump(exclude={"order_id"}),
        )
        db.add(item)
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderRead)
def get_order(order_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .options(
            selectinload(Order.items).selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment),
        )
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/{order_id}", response_model=OrderRead)
def update_order(order_id: int, data: OrderUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    payload = data.model_dump(exclude_unset=True)
    payment_methods = payload.pop("payment_methods", None)
    for k, v in payload.items():
        setattr(order, k, v)
    if payment_methods is not None:
        seen = set()
        for pm in payment_methods:
            pid = pm["payment_method_id"]
            if pid in seen:
                raise HTTPException(
                    status_code=400,
                    detail="Each payment method can only be used once per order.",
                )
            seen.add(pid)
        for opm in order.order_payments:
            db.delete(opm)
        for pm in payment_methods:
            opm = OrderPaymentMethod(
                order_id=order.id,
                payment_method_id=pm["payment_method_id"],
                amount=pm.get("amount"),
            )
            db.add(opm)
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    db.delete(order)
    db.commit()
    return None
