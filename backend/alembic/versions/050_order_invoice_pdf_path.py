"""Add invoice PDF path on orders.

Revision ID: 050
Revises: 049
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("invoice_pdf_path", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "invoice_pdf_path")
