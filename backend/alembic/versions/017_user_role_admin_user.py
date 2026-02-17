"""Add role column to users (admin/user, default admin).

Revision ID: 017
Revises: 016
Create Date: 2025-02-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(50), nullable=True))
    op.execute("UPDATE users SET role = 'admin' WHERE role IS NULL")
    op.alter_column("users", "role", nullable=False, server_default="admin")


def downgrade() -> None:
    op.drop_column("users", "role")
