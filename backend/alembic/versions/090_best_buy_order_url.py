"""Save the original Best Buy order link for tracking checks."""
from alembic import op
import sqlalchemy as sa

revision = "090"
down_revision = "089"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("best_buy_order_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("orders", "best_buy_order_url")
