"""Add received-check schedule and pending receiving review snapshots.

Revision ID: 065
Revises: 064
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "buying_groups",
        sa.Column(
            "received_check_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "buying_groups",
        sa.Column(
            "received_check_cron",
            sa.String(64),
            nullable=False,
            server_default="0 */6 * * *",
        ),
    )
    op.add_column(
        "buying_groups",
        sa.Column("received_check_last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "receiving_review_pending",
        sa.Column("buying_group_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["buying_group_id"], ["buying_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("buying_group_id"),
    )


def downgrade() -> None:
    op.drop_table("receiving_review_pending")
    op.drop_column("buying_groups", "received_check_last_run_at")
    op.drop_column("buying_groups", "received_check_cron")
    op.drop_column("buying_groups", "received_check_enabled")
