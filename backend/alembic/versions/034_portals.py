"""Add portals table.

Revision ID: 034
Revises: 033
Create Date: 2025-02-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_portals_id"), "portals", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_portals_id"), table_name="portals")
    op.drop_table("portals")
