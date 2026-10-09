"""Add api_framework, api_user_id, and api_email to buying_groups.

Revision ID: 060
Revises: 059
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("buying_groups", sa.Column("api_framework", sa.String(50), nullable=True))
    op.add_column("buying_groups", sa.Column("api_user_id", sa.Integer(), nullable=True))
    op.add_column("buying_groups", sa.Column("api_email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("buying_groups", "api_email")
    op.drop_column("buying_groups", "api_user_id")
    op.drop_column("buying_groups", "api_framework")
