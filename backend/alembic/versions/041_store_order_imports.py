"""Store order imports table for external store payloads.

Revision ID: 041
Revises: 040
Create Date: 2026-03-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_order_imports",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("store", sa.String(length=50), nullable=False),
        sa.Column("external_order_id", sa.String(length=255), nullable=False),
        sa.Column("external_order_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("linked_order_id", sa.Integer, sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False),
        sa.Column("normalized_payload_json", sa.JSON(), nullable=False),
        sa.Column("diff_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(
        "ix_store_order_imports_store_external_order_id",
        "store_order_imports",
        ["store", "external_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_store_order_imports_store_external_order_id", table_name="store_order_imports")
    op.drop_table("store_order_imports")

