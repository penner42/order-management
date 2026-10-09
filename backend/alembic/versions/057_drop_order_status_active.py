"""Drop unused order status 'active'; keep imported | personal.

Revision ID: 057
Revises: 056
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE orders SET status = 'imported' WHERE status = 'active'")
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.String(length=20),
        server_default="imported",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.String(length=20),
        server_default="active",
        existing_nullable=False,
    )
    # Do not convert imported back to active — that would hide most orders again.
