"""Store selected Best Buy order API details."""
from alembic import op
import sqlalchemy as sa

revision = "091"
down_revision = "090"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("best_buy_details", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("orders", "best_buy_details")
