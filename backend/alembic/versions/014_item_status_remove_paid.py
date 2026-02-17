"""Remove unused 'paid' from itemstatus enum.

Revision ID: 014
Revises: 013
Create Date: 2025-02-15

"""
from typing import Sequence, Union

from alembic import op


revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Current enum values with 'paid' removed (013 already migrated paid -> payment_received)
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
    "returned",
    "refunded",
)

OLD_ENUM_VALUES = NEW_ENUM_VALUES + ("paid",)


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
            CASE WHEN status::text IN ('payment_sent', 'payment_received')
            THEN 'paid'::itemstatus_old
            ELSE status::text::itemstatus_old
            END
        )
    """)
    op.execute("DROP TYPE itemstatus")
    op.execute("ALTER TYPE itemstatus_old RENAME TO itemstatus")
