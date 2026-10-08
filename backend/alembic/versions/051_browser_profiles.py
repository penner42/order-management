"""Add browser_profiles for automated store import sessions.

Revision ID: 051
Revises: 050
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "browser_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_account_id", sa.Integer(), nullable=False),
        sa.Column("retailer", sa.String(length=64), nullable=False, server_default="amazon"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="logged_out"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["store_account_id"], ["store_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_account_id", name="uq_browser_profiles_store_account_id"),
    )
    op.create_index(op.f("ix_browser_profiles_id"), "browser_profiles", ["id"], unique=False)
    op.create_index(
        op.f("ix_browser_profiles_store_account_id"),
        "browser_profiles",
        ["store_account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_browser_profiles_store_account_id"), table_name="browser_profiles")
    op.drop_index(op.f("ix_browser_profiles_id"), table_name="browser_profiles")
    op.drop_table("browser_profiles")
