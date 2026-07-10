"""Add order-level insurance cost on orders.

Revision ID: 049
Revises: 048
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "insurance_cost",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("orders", "insurance_cost", server_default=None)


def downgrade() -> None:
    op.drop_column("orders", "insurance_cost")
