"""Add tracking-submit schedule columns to buying_groups.

Revision ID: 061
Revises: 060
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "buying_groups",
        sa.Column(
            "tracking_submit_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "buying_groups",
        sa.Column(
            "tracking_submit_cron",
            sa.String(64),
            nullable=False,
            server_default="0 */6 * * *",
        ),
    )
    op.add_column(
        "buying_groups",
        sa.Column("tracking_submit_last_run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("buying_groups", "tracking_submit_last_run_at")
    op.drop_column("buying_groups", "tracking_submit_cron")
    op.drop_column("buying_groups", "tracking_submit_enabled")
