"""Add unique constraint on payment_line_items.item_id (each item in at most one payment).

Revision ID: 030
Revises: 029
Create Date: 2025-02-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_payment_line_items_item_id"), table_name="payment_line_items")
    op.create_unique_constraint("uq_payment_line_item_item", "payment_line_items", ["item_id"])


def downgrade() -> None:
    op.drop_constraint("uq_payment_line_item_item", "payment_line_items", type_="unique")
    op.create_index(op.f("ix_payment_line_items_item_id"), "payment_line_items", ["item_id"], unique=False)
