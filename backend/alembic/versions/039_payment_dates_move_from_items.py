"""Add payment_requested_at, payment_sent_at, payment_received_at to payments; move from items; remove PAYMENT_* from itemstatus.

Revision ID: 039
Revises: 038
Create Date: 2026-02-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ItemStatus without payment statuses (status determined by payment dates now)
NEW_ENUM_VALUES = (
    "purchased",
    "shipped",
    "submitted",
    "scanned",
    "canceled",
    "needs_return",
    "return_started",
    "return_sent",
    "return_received",
    "return_refunded",
)

OLD_ENUM_VALUES = NEW_ENUM_VALUES + (
    "payment_requested",
    "payment_sent",
    "payment_received",
)


def upgrade() -> None:
    # 1. Add payment date columns to payments
    op.add_column(
        "payments",
        sa.Column("payment_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("payment_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("payment_received_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Migrate data: for each payment, set dates from its line items (min requested/sent, max received)
    op.execute(
        """
        UPDATE payments p
        SET
            payment_requested_at = sub.req,
            payment_sent_at = sub.sent,
            payment_received_at = sub.rec
        FROM (
            SELECT
                pli.payment_id,
                MIN(i.payment_requested_at) AS req,
                MIN(i.payment_sent_at) AS sent,
                MAX(i.payment_received_at) AS rec
            FROM payment_line_items pli
            JOIN items i ON i.id = pli.item_id
            GROUP BY pli.payment_id
        ) sub
        WHERE p.id = sub.payment_id
        """
    )

    # 3. Drop payment date columns from items
    op.drop_column("items", "payment_requested_at")
    op.drop_column("items", "payment_sent_at")
    op.drop_column("items", "payment_received_at")

    # 4. Map item statuses to 'scanned' and remove payment values from enum
    op.execute(
        """
        UPDATE items
        SET status = 'scanned'
        WHERE status IN ('payment_requested', 'payment_sent', 'payment_received')
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
    # Restore enum with payment values
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

    # Add payment date columns back to items
    op.add_column(
        "items",
        sa.Column("payment_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("payment_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("payment_received_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Copy payment dates back from payment to items (each line item gets its payment's dates)
    op.execute(
        """
        UPDATE items i
        SET
            payment_requested_at = p.payment_requested_at,
            payment_sent_at = p.payment_sent_at,
            payment_received_at = p.payment_received_at
        FROM payment_line_items pli
        JOIN payments p ON p.id = pli.payment_id
        WHERE pli.item_id = i.id
        """
    )

    # Restore item status from payment dates (last state wins: received > sent > requested)
    op.execute(
        """
        UPDATE items i
        SET status = CASE
            WHEN p.payment_received_at IS NOT NULL THEN 'payment_received'
            WHEN p.payment_sent_at IS NOT NULL THEN 'payment_sent'
            WHEN p.payment_requested_at IS NOT NULL THEN 'payment_requested'
            ELSE i.status
        END
        FROM payment_line_items pli
        JOIN payments p ON p.id = pli.payment_id
        WHERE pli.item_id = i.id
        """
    )

    # Drop payment date columns from payments
    op.drop_column("payments", "payment_received_at")
    op.drop_column("payments", "payment_sent_at")
    op.drop_column("payments", "payment_requested_at")
