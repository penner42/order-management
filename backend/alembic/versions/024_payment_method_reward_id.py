"""Link payment methods to rewards (reward_id on payment_methods).

Revision ID: 024
Revises: 023
Create Date: 2025-02-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_methods", sa.Column("reward_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_payment_methods_reward_id_rewards",
        "payment_methods",
        "rewards",
        ["reward_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_payment_methods_reward_id_rewards", "payment_methods", type_="foreignkey")
    op.drop_column("payment_methods", "reward_id")
