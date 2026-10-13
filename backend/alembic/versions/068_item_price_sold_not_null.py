"""Make items.price_sold required; backfill nulls to 0.

Revision ID: 068
Revises: 067
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE items
        SET price_sold = 0
        WHERE price_sold IS NULL
        """
    )
    op.alter_column(
        "items",
        "price_sold",
        existing_type=sa.Numeric(12, 2),
        nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.alter_column(
        "items",
        "price_sold",
        existing_type=sa.Numeric(12, 2),
        nullable=True,
        server_default=None,
    )
