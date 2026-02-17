"""Move buying_group from items to orders.

Revision ID: 012
Revises: 011
Create Date: 2025-02-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("buying_group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_buying_group_id_buying_groups",
        "orders",
        "buying_groups",
        ["buying_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Migrate: set order.buying_group_id from first item that has one
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE orders o
            SET buying_group_id = (
                SELECT i.buying_group_id FROM items i
                WHERE i.order_id = o.id AND i.buying_group_id IS NOT NULL
                LIMIT 1
            )
        """)
    )
    op.drop_constraint("items_buying_group_id_fkey", "items", type_="foreignkey")
    op.drop_column("items", "buying_group_id")


def downgrade() -> None:
    op.add_column(
        "items",
        sa.Column("buying_group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "items_buying_group_id_fkey",
        "items",
        "buying_groups",
        ["buying_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Copy order's buying_group_id back to all its items
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE items
            SET buying_group_id = (SELECT buying_group_id FROM orders WHERE orders.id = items.order_id)
        """)
    )
    op.drop_constraint("fk_orders_buying_group_id_buying_groups", "orders", type_="foreignkey")
    op.drop_column("orders", "buying_group_id")
