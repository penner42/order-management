"""Add shipping and sales_tax to items (per item).

Revision ID: 035
Revises: 034
Create Date: 2025-02-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add shipping and sales_tax columns to items
    op.add_column("items", sa.Column("shipping", sa.Numeric(12, 2), nullable=True))
    op.add_column("items", sa.Column("sales_tax", sa.Numeric(12, 2), nullable=True))
    
    # Migrate existing order-level shipping and tax to items proportionally
    # This distributes order shipping/tax to items based on their price_paid proportion
    conn = op.get_bind()
    
    # Get all orders with shipping or sales_tax
    orders_with_totals = conn.execute(sa.text("""
        SELECT o.id, o.shipping, o.sales_tax,
               COALESCE(SUM(i.price_paid * COALESCE(i.quantity, 1)), 0) as total_item_price
        FROM orders o
        LEFT JOIN items i ON i.order_id = o.id
        WHERE o.shipping IS NOT NULL OR o.sales_tax IS NOT NULL
        GROUP BY o.id, o.shipping, o.sales_tax
    """))
    
    for order_row in orders_with_totals:
        order_id = order_row[0]
        order_shipping = order_row[1]
        order_sales_tax = order_row[2]
        total_item_price = float(order_row[3] or 0)
        
        # Get all items for this order
        items = conn.execute(sa.text("""
            SELECT id, price_paid, quantity
            FROM items
            WHERE order_id = :order_id
        """), {"order_id": order_id}).fetchall()
        
        if not items:
            continue
        
        # Distribute shipping and tax proportionally based on price_paid
        # If all items have no price or same price, distribute evenly
        if total_item_price > 0:
            # Proportional distribution
            for item in items:
                item_id = item[0]
                item_price = float(item[1] or 0)
                item_qty = int(item[2] or 1)
                item_total = item_price * item_qty
                
                if order_shipping is not None:
                    item_shipping = (item_total / total_item_price) * float(order_shipping)
                    conn.execute(sa.text("""
                        UPDATE items SET shipping = :shipping WHERE id = :item_id
                    """), {"shipping": item_shipping, "item_id": item_id})
                
                if order_sales_tax is not None:
                    item_tax = (item_total / total_item_price) * float(order_sales_tax)
                    conn.execute(sa.text("""
                        UPDATE items SET sales_tax = :tax WHERE id = :item_id
                    """), {"tax": item_tax, "item_id": item_id})
        else:
            # Even distribution
            num_items = len(items)
            if num_items > 0:
                item_shipping = float(order_shipping or 0) / num_items if order_shipping else None
                item_tax = float(order_sales_tax or 0) / num_items if order_sales_tax else None
                
                for item in items:
                    item_id = item[0]
                    if item_shipping is not None:
                        conn.execute(sa.text("""
                            UPDATE items SET shipping = :shipping WHERE id = :item_id
                        """), {"shipping": item_shipping, "item_id": item_id})
                    if item_tax is not None:
                        conn.execute(sa.text("""
                            UPDATE items SET sales_tax = :tax WHERE id = :item_id
                        """), {"tax": item_tax, "item_id": item_id})


def downgrade() -> None:
    op.drop_column("items", "sales_tax")
    op.drop_column("items", "shipping")
