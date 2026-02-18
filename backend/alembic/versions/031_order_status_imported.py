"""Add order status (active, imported). Imported orders are excluded from main list.

Revision ID: 031
Revises: 030
Create Date: 2025-02-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("status", sa.String(20), nullable=True))
    op.execute("UPDATE orders SET status = 'active' WHERE status IS NULL")
    op.alter_column("orders", "status", nullable=False, existing_type=sa.String(20))


def downgrade() -> None:
    op.drop_column("orders", "status")
