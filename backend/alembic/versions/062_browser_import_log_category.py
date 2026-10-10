"""Add category to browser_import_logs (stores | groups).

Revision ID: 062
Revises: 061
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "browser_import_logs",
        sa.Column(
            "category",
            sa.String(length=16),
            nullable=False,
            server_default="stores",
        ),
    )
    op.create_index(
        op.f("ix_browser_import_logs_category"),
        "browser_import_logs",
        ["category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_browser_import_logs_category"), table_name="browser_import_logs")
    op.drop_column("browser_import_logs", "category")
