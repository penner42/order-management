"""Add level/message to browser_import_logs; allow null store_order_number.

Revision ID: 054
Revises: 053
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "browser_import_logs",
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
    )
    op.add_column("browser_import_logs", sa.Column("message", sa.Text(), nullable=True))
    op.alter_column(
        "browser_import_logs",
        "store_order_number",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.create_index(op.f("ix_browser_import_logs_level"), "browser_import_logs", ["level"], unique=False)
    # Existing rows are order_imported / tracking_updated → updates.
    op.execute(
        "UPDATE browser_import_logs SET level = 'updates' "
        "WHERE event_type IN ('order_imported', 'tracking_updated')"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_browser_import_logs_level"), table_name="browser_import_logs")
    op.execute(
        "UPDATE browser_import_logs SET store_order_number = '' WHERE store_order_number IS NULL"
    )
    op.alter_column(
        "browser_import_logs",
        "store_order_number",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_column("browser_import_logs", "message")
    op.drop_column("browser_import_logs", "level")
