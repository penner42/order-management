"""Add order status (active, canceled).

Revision ID: 003
Revises: 002
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("status", sa.String(20), nullable=True))
    op.execute("UPDATE orders SET status = 'active' WHERE status IS NULL")
    op.alter_column("orders", "status", nullable=False, existing_type=sa.String(20))


def downgrade() -> None:
    op.drop_column("orders", "status")
