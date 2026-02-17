"""Add submission_id to items.

Revision ID: 029
Revises: 028
Create Date: 2025-02-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("submission_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "submission_id")
