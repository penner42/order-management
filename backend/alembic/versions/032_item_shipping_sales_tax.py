"""Add shipping and sales_tax to items (per line item).

Revision ID: 032
Revises: 031
Create Date: 2025-02-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("shipping", sa.Numeric(12, 2), nullable=True))
    op.add_column("items", sa.Column("sales_tax", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "sales_tax")
    op.drop_column("items", "shipping")
