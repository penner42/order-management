"""Remove order status column (revert canceled status feature).

Revision ID: 004
Revises: 003
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("orders", "status")


def downgrade() -> None:
    op.add_column("orders", sa.Column("status", sa.String(20), nullable=True, server_default="active"))
    op.alter_column("orders", "status", nullable=False, existing_type=sa.String(20))
