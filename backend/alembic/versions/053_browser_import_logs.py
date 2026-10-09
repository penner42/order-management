"""Add browser_import_logs for scheduled/auto-apply import activity.

Revision ID: 053
Revises: 052
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "browser_import_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("browser_profile_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("retailer", sa.String(length=64), nullable=False),
        sa.Column("store_order_number", sa.String(length=255), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("tracking_numbers", sa.Text(), nullable=True),
        sa.Column("store_name", sa.String(length=255), nullable=True),
        sa.Column("store_account_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["browser_profile_id"], ["browser_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_browser_import_logs_id"), "browser_import_logs", ["id"], unique=False)
    op.create_index(
        op.f("ix_browser_import_logs_browser_profile_id"),
        "browser_import_logs",
        ["browser_profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_import_logs_job_id"),
        "browser_import_logs",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_import_logs_event_type"),
        "browser_import_logs",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_import_logs_store_order_number"),
        "browser_import_logs",
        ["store_order_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_import_logs_order_id"),
        "browser_import_logs",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_import_logs_created_at"),
        "browser_import_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_browser_import_logs_created_at"), table_name="browser_import_logs")
    op.drop_index(op.f("ix_browser_import_logs_order_id"), table_name="browser_import_logs")
    op.drop_index(op.f("ix_browser_import_logs_store_order_number"), table_name="browser_import_logs")
    op.drop_index(op.f("ix_browser_import_logs_event_type"), table_name="browser_import_logs")
    op.drop_index(op.f("ix_browser_import_logs_job_id"), table_name="browser_import_logs")
    op.drop_index(op.f("ix_browser_import_logs_browser_profile_id"), table_name="browser_import_logs")
    op.drop_index(op.f("ix_browser_import_logs_id"), table_name="browser_import_logs")
    op.drop_table("browser_import_logs")
