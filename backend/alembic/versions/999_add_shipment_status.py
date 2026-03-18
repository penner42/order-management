"""Add status field to shipments.

Revision ID: 999_add_shipment_status
Revises: 046
Create Date: 2026-03-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "999_add_shipment_status"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("status", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("shipments", "status")

