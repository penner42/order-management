"""Add datetime column per item status (when each status was set).

Revision ID: 018
Revises: 017
Create Date: 2025-02-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("payment_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("payment_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("payment_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("needs_return_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("return_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("return_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("return_refunded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "return_refunded_at")
    op.drop_column("items", "return_received_at")
    op.drop_column("items", "return_sent_at")
    op.drop_column("items", "needs_return_at")
    op.drop_column("items", "canceled_at")
    op.drop_column("items", "payment_received_at")
    op.drop_column("items", "payment_sent_at")
    op.drop_column("items", "payment_requested_at")
    op.drop_column("items", "scanned_at")
    op.drop_column("items", "delivered_at")
    op.drop_column("items", "shipped_at")
    op.drop_column("items", "purchased_at")
