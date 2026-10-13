"""Add needs_review to item status enum and needs_review_at column.

Revision ID: 066
Revises: 065
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'needs_review'")
    op.add_column(
        "items",
        sa.Column("needs_review_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "needs_review_at")
    # PostgreSQL does not support removing a value from an enum; leave needs_review in type
