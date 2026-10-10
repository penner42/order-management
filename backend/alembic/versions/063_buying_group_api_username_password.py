"""Add api_username and api_password to buying_groups (USABG login).

Revision ID: 063
Revises: 062
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("buying_groups", sa.Column("api_username", sa.String(255), nullable=True))
    op.add_column("buying_groups", sa.Column("api_password", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("buying_groups", "api_password")
    op.drop_column("buying_groups", "api_username")
