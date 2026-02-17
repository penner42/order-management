"""Add rewards table.

Revision ID: 023
Revises: 022
Create Date: 2025-02-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rewards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_rewards_id"), "rewards", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_rewards_id"), table_name="rewards")
    op.drop_table("rewards")
