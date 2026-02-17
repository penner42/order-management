"""Add needs_return, returned, refunded to item status enum.

Revision ID: 006
Revises: 005
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'needs_return'")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'returned'")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'refunded'")


def downgrade() -> None:
    # PostgreSQL does not support removing a value from an enum easily; leave as-is
    pass
