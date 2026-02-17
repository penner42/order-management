"""Add store_order_number to orders.

Revision ID: 008
Revises: 007
Create Date: 2025-02-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("store_order_number", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "store_order_number")
