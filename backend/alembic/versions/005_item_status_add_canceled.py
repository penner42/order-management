"""Add canceled to item status enum.

Revision ID: 005
Revises: 004
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'canceled'")


def downgrade() -> None:
    # PostgreSQL does not support removing a value from an enum easily; leave as-is
    pass
