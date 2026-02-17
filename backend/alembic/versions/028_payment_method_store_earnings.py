"""Add payment_method_store_earnings table (points per dollar per store).

Revision ID: 028
Revises: 027
Create Date: 2025-02-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_method_store_earnings",
        sa.Column("payment_method_id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("points_per_dollar", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["payment_method_id"],
            ["payment_methods.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("payment_method_id", "store_id"),
    )
    op.create_index(
        op.f("ix_payment_method_store_earnings_payment_method_id"),
        "payment_method_store_earnings",
        ["payment_method_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_method_store_earnings_store_id"),
        "payment_method_store_earnings",
        ["store_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_payment_method_store_earnings_store_id"),
        table_name="payment_method_store_earnings",
    )
    op.drop_index(
        op.f("ix_payment_method_store_earnings_payment_method_id"),
        table_name="payment_method_store_earnings",
    )
    op.drop_table("payment_method_store_earnings")
