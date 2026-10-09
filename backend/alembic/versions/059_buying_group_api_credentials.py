"""Add base_url, api_url, and bearer_token to buying_groups.

Revision ID: 059
Revises: 058
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("buying_groups", sa.Column("base_url", sa.String(500), nullable=True))
    op.add_column("buying_groups", sa.Column("api_url", sa.String(500), nullable=True))
    op.add_column("buying_groups", sa.Column("bearer_token", sa.String(2000), nullable=True))


def downgrade() -> None:
    op.drop_column("buying_groups", "bearer_token")
    op.drop_column("buying_groups", "api_url")
    op.drop_column("buying_groups", "base_url")
