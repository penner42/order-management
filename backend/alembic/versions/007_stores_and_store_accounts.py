"""Add stores, store_accounts, and order store/store_account_id.

Revision ID: 007
Revises: 006
Create Date: 2025-02-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create stores table
    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stores_id"), "stores", ["id"], unique=False)

    # Create store_accounts table
    op.create_table(
        "store_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_store_accounts_id"), "store_accounts", ["id"], unique=False)

    # Insert default store for existing orders
    op.execute(
        "INSERT INTO stores (id, name, created_at) VALUES (1, 'Default', now())"
    )

    # Add store_id and store_account_id to orders
    op.add_column("orders", sa.Column("store_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("store_account_id", sa.Integer(), nullable=True))

    # Update existing orders to use default store
    op.execute("UPDATE orders SET store_id = 1 WHERE store_id IS NULL")

    # Make store_id NOT NULL
    op.alter_column(
        "orders",
        "store_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.create_foreign_key(
        "fk_orders_store_id",
        "orders",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_orders_store_account_id",
        "orders",
        "store_accounts",
        ["store_account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_orders_store_account_id", "orders", type_="foreignkey")
    op.drop_constraint("fk_orders_store_id", "orders", type_="foreignkey")
    op.drop_column("orders", "store_account_id")
    op.drop_column("orders", "store_id")
    op.drop_table("store_accounts")
    op.drop_table("stores")
