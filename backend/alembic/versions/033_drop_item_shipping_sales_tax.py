"""Ensure shipping and sales_tax are on orders only (not on items).

- If orders are missing these columns (e.g. DB ran old 032 that added to items),
  adds them to orders.
- Drops from items if present. Safe if columns already missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    order_cols = {c["name"] for c in insp.get_columns("orders")}
    if "shipping" not in order_cols:
        op.add_column("orders", sa.Column("shipping", sa.Numeric(12, 2), nullable=True))
    if "sales_tax" not in order_cols:
        op.add_column("orders", sa.Column("sales_tax", sa.Numeric(12, 2), nullable=True))
    item_cols = {c["name"] for c in insp.get_columns("items")}
    if "shipping" in item_cols:
        op.drop_column("items", "shipping")
    if "sales_tax" in item_cols:
        op.drop_column("items", "sales_tax")


def downgrade() -> None:
    op.add_column("items", sa.Column("shipping", sa.Numeric(12, 2), nullable=True))
    op.add_column("items", sa.Column("sales_tax", sa.Numeric(12, 2), nullable=True))
    op.drop_column("orders", "sales_tax")
    op.drop_column("orders", "shipping")
