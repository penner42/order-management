"""Remove 'delivered' from itemstatus enum; rely on shipments.delivered_at.

Revision ID: 038
Revises: 037
Create Date: 2026-02-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_ENUM_VALUES = (
    "purchased",
    "shipped",
    "submitted",
    "scanned",
    "payment_requested",
    "payment_sent",
    "payment_received",
    "canceled",
    "needs_return",
    "return_started",
    "return_sent",
    "return_received",
    "return_refunded",
)

OLD_ENUM_VALUES = NEW_ENUM_VALUES + ("delivered",)


def upgrade() -> None:
    # Map existing 'delivered' statuses to 'submitted' before removing the enum value.
    op.execute(
        """
        UPDATE items
        SET status = 'submitted'
        WHERE status = 'delivered'
        """
    )

    op.execute(
        "CREATE TYPE itemstatus_new AS ENUM ("
        + ", ".join(f"'{v}'" for v in NEW_ENUM_VALUES)
        + ")"
    )
    op.execute(
        """
        ALTER TABLE items
        ALTER COLUMN status
        TYPE itemstatus_new
        USING status::text::itemstatus_new
        """
    )
    op.execute("DROP TYPE itemstatus")
    op.execute("ALTER TYPE itemstatus_new RENAME TO itemstatus")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE itemstatus_old AS ENUM ("
        + ", ".join(f"'{v}'" for v in OLD_ENUM_VALUES)
        + ")"
    )
    op.execute(
        """
        ALTER TABLE items
        ALTER COLUMN status
        TYPE itemstatus_old
        USING status::text::itemstatus_old
        """
    )
    op.execute("DROP TYPE itemstatus")
    op.execute("ALTER TYPE itemstatus_old RENAME TO itemstatus")

