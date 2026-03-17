"""Add payment bonus amount on payments.

Revision ID: 046
Revises: 045
Create Date: 2026-03-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "payment_bonus",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("payments", "payment_bonus", server_default=None)


def downgrade() -> None:
    op.drop_column("payments", "payment_bonus")

