"""Add lost_package to item status enum and lost_package_at column.

Revision ID: 058
Revises: 057
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'lost_package'")
    op.add_column(
        "items",
        sa.Column("lost_package_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "lost_package_at")
    # PostgreSQL does not support removing a value from an enum; leave lost_package in type
