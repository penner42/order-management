"""Represent items that do not require a payout without a payment record."""
from alembic import op

revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE itemstatus ADD VALUE IF NOT EXISTS 'no_payment_necessary'")


def downgrade():
    # PostgreSQL enum additions cannot be removed without rebuilding the type.
    pass
