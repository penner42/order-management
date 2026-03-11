"""Make (store, external_order_id) unique on store_order_imports.

Revision ID: 042
Revises: 041
Create Date: 2026-03-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicate rows, keeping the most recently created import per
    # (store, external_order_id) pair so the unique constraint can be added.
    op.execute(
        sa.text("""
            DELETE FROM store_order_imports
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM store_order_imports
                GROUP BY store, external_order_id
            )
        """)
    )

    op.drop_index(
        "ix_store_order_imports_store_external_order_id",
        table_name="store_order_imports",
    )
    op.create_unique_constraint(
        "uq_store_order_imports_store_external_order_id",
        "store_order_imports",
        ["store", "external_order_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_store_order_imports_store_external_order_id",
        "store_order_imports",
        type_="unique",
    )
    op.create_index(
        "ix_store_order_imports_store_external_order_id",
        "store_order_imports",
        ["store", "external_order_id"],
    )
