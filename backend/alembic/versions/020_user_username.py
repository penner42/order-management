"""Add username to users for login; email nullable for admin-only accounts.

Revision ID: 020
Revises: 019
Create Date: 2025-02-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(255), nullable=True))
    op.execute("UPDATE users SET username = email WHERE username IS NULL")
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(255),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_column("users", "username")
