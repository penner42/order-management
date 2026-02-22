"""Add indexes on orders for list/filter/sort scalability.

Revision ID: 037
Revises: 036
Create Date: 2025-02-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)
    op.create_index(op.f("ix_orders_purchase_date"), "orders", ["purchase_date"], unique=False)
    op.create_index(op.f("ix_orders_buying_group_id"), "orders", ["buying_group_id"], unique=False)
    op.create_index(
        op.f("ix_orders_status_purchase_date"),
        "orders",
        ["status", "purchase_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_status_purchase_date"), table_name="orders")
    op.drop_index(op.f("ix_orders_buying_group_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_purchase_date"), table_name="orders")
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
