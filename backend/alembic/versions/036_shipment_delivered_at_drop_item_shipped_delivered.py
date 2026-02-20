"""Add shipments.delivered_at; drop items.shipped_at and items.delivered_at.

Revision ID: 036
Revises: 035
Create Date: 2025-02-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shipments",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("items", "shipped_at")
    op.drop_column("items", "delivered_at")


def downgrade() -> None:
    op.add_column(
        "items",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("shipments", "delivered_at")
