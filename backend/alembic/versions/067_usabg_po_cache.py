"""Add usabg_po_cache for durable USABG PO detail reuse.

Revision ID: 067
Revises: 066
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usabg_po_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("buying_group_id", sa.Integer(), nullable=False),
        sa.Column("po_number", sa.String(length=64), nullable=False),
        sa.Column("receiving_status", sa.String(length=64), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["buying_group_id"], ["buying_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("buying_group_id", "po_number", name="uq_usabg_po_cache_bg_po"),
    )
    op.create_index(
        op.f("ix_usabg_po_cache_buying_group_id"),
        "usabg_po_cache",
        ["buying_group_id"],
        unique=False,
    )
    op.create_index(op.f("ix_usabg_po_cache_id"), "usabg_po_cache", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_usabg_po_cache_id"), table_name="usabg_po_cache")
    op.drop_index(op.f("ix_usabg_po_cache_buying_group_id"), table_name="usabg_po_cache")
    op.drop_table("usabg_po_cache")
