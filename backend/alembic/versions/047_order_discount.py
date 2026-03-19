"""Add order-level discount amount on orders.

Revision ID: 047
Revises: 999_add_shipment_status
Create Date: 2026-03-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "047"
down_revision: Union[str, None] = "999_add_shipment_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "order_discount",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("orders", "order_discount", server_default=None)


def downgrade() -> None:
    op.drop_column("orders", "order_discount")

