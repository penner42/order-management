"""Add ignored_zip_codes table.

Revision ID: 056
Revises: 055
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ignored_zip_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zip_code", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("zip_code"),
    )
    op.create_index(op.f("ix_ignored_zip_codes_id"), "ignored_zip_codes", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ignored_zip_codes_id"), table_name="ignored_zip_codes")
    op.drop_table("ignored_zip_codes")
