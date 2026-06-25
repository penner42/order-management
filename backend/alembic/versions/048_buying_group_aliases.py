"""Add aliases to buying groups for import name matching.

Revision ID: 048
Revises: 047
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "buying_groups",
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("buying_groups", "aliases", server_default=None)


def downgrade() -> None:
    op.drop_column("buying_groups", "aliases")
