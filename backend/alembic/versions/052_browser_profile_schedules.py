"""Add per-profile full-check and unshipped-check schedules.

Revision ID: 052
Revises: 051
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "browser_profiles",
        sa.Column("full_check_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "browser_profiles",
        sa.Column("full_check_interval_hours", sa.Integer(), nullable=False, server_default="24"),
    )
    op.add_column(
        "browser_profiles",
        sa.Column("full_check_max_pages", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "browser_profiles",
        sa.Column("full_check_last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "browser_profiles",
        sa.Column("unshipped_check_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "browser_profiles",
        sa.Column("unshipped_check_interval_hours", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column(
        "browser_profiles",
        sa.Column("unshipped_check_last_run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("browser_profiles", "unshipped_check_last_run_at")
    op.drop_column("browser_profiles", "unshipped_check_interval_hours")
    op.drop_column("browser_profiles", "unshipped_check_enabled")
    op.drop_column("browser_profiles", "full_check_last_run_at")
    op.drop_column("browser_profiles", "full_check_max_pages")
    op.drop_column("browser_profiles", "full_check_interval_hours")
    op.drop_column("browser_profiles", "full_check_enabled")
