"""Add submitted to item status enum (between shipped and delivered) and submitted_at column.

Revision ID: 021
Revises: 020
Create Date: 2025-02-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A fresh install runs all revisions in one invocation. PostgreSQL must
    # commit an enum addition before later revisions can use the new value.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'submitted'")
    op.add_column(
        "items",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "submitted_at")
    # PostgreSQL does not support removing a value from an enum; leave submitted in type
