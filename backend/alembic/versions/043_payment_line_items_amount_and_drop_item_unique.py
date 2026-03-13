"""Add amount to payment_line_items and allow multiple payments per item.

Revision ID: 043
Revises: 042
Create Date: 2026-03-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop unique constraint that forced each item into at most one payment
    op.drop_constraint("uq_payment_line_item_item", "payment_line_items", type_="unique")

    # Add amount column, nullable for backfill
    op.add_column(
        "payment_line_items",
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
    )

    # Backfill existing rows with full item total (price_sold * quantity)
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE payment_line_items pli
            SET amount = COALESCE(i.price_sold, 0) * COALESCE(i.quantity, 1)
            FROM items i
            WHERE pli.item_id = i.id
            """
        )
    )

    # Make amount non-nullable after backfill
    op.alter_column("payment_line_items", "amount", nullable=False)


def downgrade() -> None:
    # Allow amount to be nullable during downgrade
    op.alter_column("payment_line_items", "amount", nullable=True)

    # Drop amount column
    op.drop_column("payment_line_items", "amount")

    # Recreate unique constraint on item_id (matches revision 030)
    op.create_unique_constraint("uq_payment_line_item_item", "payment_line_items", ["item_id"])

