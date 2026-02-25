"""Make payment_requested_at required; backfill from created_at where null.

Revision ID: 040
Revises: 039
Create Date: 2026-02-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE payments
        SET payment_requested_at = created_at
        WHERE payment_requested_at IS NULL
        """
    )
    op.alter_column(
        "payments",
        "payment_requested_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "payments",
        "payment_requested_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
