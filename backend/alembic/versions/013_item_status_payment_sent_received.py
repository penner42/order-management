"""Replace paid with payment_sent and payment_received.

Revision ID: 013
Revises: 012
Create Date: 2025-02-15

"""
from typing import Sequence, Union

from alembic import op


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE commits in PG; new values aren't visible until after commit.
    # So commit, add values, then start a new transaction for the UPDATE.
    op.execute("COMMIT")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'payment_sent'")
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'payment_received'")
    op.execute("BEGIN")
    op.execute("UPDATE items SET status = 'payment_received' WHERE status = 'paid'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; optionally revert data
    op.execute("UPDATE items SET status = 'paid' WHERE status IN ('payment_sent', 'payment_received')")
