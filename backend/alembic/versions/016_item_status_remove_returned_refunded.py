"""Remove unused 'returned' and 'refunded' from itemstatus enum.

Revision ID: 016
Revises: 015
Create Date: 2025-02-15

"""
from typing import Sequence, Union

from alembic import op


revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 014 enum had: purchased, shipped, delivered, scanned, payment_requested,
# payment_sent, payment_received, canceled, needs_return, returned, refunded.
# Replace returned/refunded with return_sent, return_received, return_refunded.
NEW_ENUM_VALUES = (
    "purchased",
    "shipped",
    "delivered",
    "scanned",
    "payment_requested",
    "payment_sent",
    "payment_received",
    "canceled",
    "needs_return",
    "return_sent",
    "return_received",
    "return_refunded",
)

OLD_ENUM_VALUES = (
    "purchased",
    "shipped",
    "delivered",
    "scanned",
    "payment_requested",
    "payment_sent",
    "payment_received",
    "canceled",
    "needs_return",
    "returned",
    "refunded",
    "return_sent",
    "return_received",
    "return_refunded",
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE itemstatus_new AS ENUM ("
        + ", ".join(f"'{v}'" for v in NEW_ENUM_VALUES)
        + ")"
    )
    op.execute(
        "ALTER TABLE items ALTER COLUMN status TYPE itemstatus_new USING status::text::itemstatus_new"
    )
    op.execute("DROP TYPE itemstatus")
    op.execute("ALTER TYPE itemstatus_new RENAME TO itemstatus")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE itemstatus_old AS ENUM ("
        + ", ".join(f"'{v}'" for v in OLD_ENUM_VALUES)
        + ")"
    )
    op.execute("""
        ALTER TABLE items ALTER COLUMN status TYPE itemstatus_old USING (
            CASE
                WHEN status::text = 'return_received' THEN 'returned'::itemstatus_old
                WHEN status::text = 'return_refunded' THEN 'refunded'::itemstatus_old
                WHEN status::text = 'return_sent' THEN 'returned'::itemstatus_old
                ELSE status::text::itemstatus_old
            END
        )
    """)
    op.execute("DROP TYPE itemstatus")
    op.execute("ALTER TYPE itemstatus_old RENAME TO itemstatus")
