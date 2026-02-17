"""One shipment per item: unique constraint on shipment_items.item_id.

Revision ID: 010
Revises: 009
Create Date: 2025-02-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicates: keep one shipment_item per item_id (smallest id)
    op.execute("""
        DELETE FROM shipment_items a
        USING shipment_items b
        WHERE a.item_id = b.item_id AND a.id > b.id
    """)
    op.create_unique_constraint(
        "uq_shipment_items_item_id",
        "shipment_items",
        ["item_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_shipment_items_item_id", "shipment_items", type_="unique")
