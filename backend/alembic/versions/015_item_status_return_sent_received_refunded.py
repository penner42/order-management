"""Add return_sent, return_received, return_refunded; migrate from returned/refunded.

Revision ID: 015
Revises: 014
Create Date: 2025-02-15

"""
from typing import Sequence, Union

from alembic import op


revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE commits in PG; new values aren't visible until after commit.
    op.execute("COMMIT")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'return_sent'")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'return_received'")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'return_refunded'")
    op.execute("BEGIN")
    op.execute("UPDATE items SET status = 'return_received' WHERE status = 'returned'")
    op.execute("UPDATE items SET status = 'return_refunded' WHERE status = 'refunded'")


def downgrade() -> None:
    op.execute("UPDATE items SET status = 'returned' WHERE status = 'return_received'")
    op.execute("UPDATE items SET status = 'refunded' WHERE status = 'return_refunded'")
    op.execute("UPDATE items SET status = 'returned' WHERE status = 'return_sent'")
