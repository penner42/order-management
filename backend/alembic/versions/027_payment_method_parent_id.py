"""Add parent_id to payment_methods for sub-methods.

Revision ID: 027
Revises: 026
Create Date: 2025-02-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_methods", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_payment_methods_parent_id_payment_methods",
        "payment_methods",
        "payment_methods",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_payment_methods_parent_id_payment_methods",
        "payment_methods",
        type_="foreignkey",
    )
    op.drop_column("payment_methods", "parent_id")
