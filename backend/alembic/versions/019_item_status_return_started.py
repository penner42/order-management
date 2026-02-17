"""Add return_started to item status enum and return_started_at column.

Revision ID: 019
Revises: 018
Create Date: 2025-02-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'return_started'")
    op.add_column(
        "items",
        sa.Column("return_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "return_started_at")
    # PostgreSQL does not support removing a value from an enum; leave return_started in type
