"""Add receipt_id to items.

Revision ID: 025
Revises: 024
Create Date: 2025-02-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("receipt_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "receipt_id")
