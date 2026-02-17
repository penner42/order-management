"""Payment methods: remove enum and extra fields, keep single label.

Revision ID: 002
Revises: 001
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("payment_methods", "paypal_card_reference")
    op.drop_column("payment_methods", "type")
    op.execute("DROP TYPE IF EXISTS paymentmethodtype")


def downgrade() -> None:
    op.execute("CREATE TYPE paymentmethodtype AS ENUM ('credit_card', 'paypal', 'other')")
    op.add_column("payment_methods", sa.Column("type", sa.Enum("credit_card", "paypal", "other", name="paymentmethodtype", create_constraint=True), nullable=True))
    op.add_column("payment_methods", sa.Column("paypal_card_reference", sa.String(255), nullable=True))