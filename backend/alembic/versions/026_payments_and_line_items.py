"""Add payments and payment_line_items tables.

Revision ID: 026
Revises: 025
Create Date: 2025-02-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("buying_group_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["buying_group_id"], ["buying_groups.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payments_id"), "payments", ["id"], unique=False)
    op.create_index(op.f("ix_payments_buying_group_id"), "payments", ["buying_group_id"], unique=False)

    op.create_table(
        "payment_line_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", "item_id", name="uq_payment_line_item_payment_item"),
    )
    op.create_index(op.f("ix_payment_line_items_id"), "payment_line_items", ["id"], unique=False)
    op.create_index(op.f("ix_payment_line_items_payment_id"), "payment_line_items", ["payment_id"], unique=False)
    op.create_index(op.f("ix_payment_line_items_item_id"), "payment_line_items", ["item_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_line_items_item_id"), table_name="payment_line_items")
    op.drop_index(op.f("ix_payment_line_items_payment_id"), table_name="payment_line_items")
    op.drop_index(op.f("ix_payment_line_items_id"), table_name="payment_line_items")
    op.drop_table("payment_line_items")
    op.drop_index(op.f("ix_payments_buying_group_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_id"), table_name="payments")
    op.drop_table("payments")
