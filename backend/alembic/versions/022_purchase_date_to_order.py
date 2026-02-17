"""Move purchase_date from items to orders (order-level field).

Revision ID: 022
Revises: 021
Create Date: 2025-02-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill from first item per order (by item id)
    op.execute("""
        UPDATE orders o
        SET purchase_date = (
            SELECT i.purchase_date
            FROM items i
            WHERE i.order_id = o.id
            ORDER BY i.id
            LIMIT 1
        )
    """)
    op.drop_column("items", "purchase_date")


def downgrade() -> None:
    op.add_column(
        "items",
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=True),
    )
    # Copy order purchase_date to all its items
    op.execute("""
        UPDATE items i
        SET purchase_date = o.purchase_date
        FROM orders o
        WHERE o.id = i.order_id
    """)
    op.drop_column("orders", "purchase_date")
