"""Store warehouse rewards separately from item payout."""
from alembic import op
import sqlalchemy as sa

revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("reward", sa.Numeric(12, 2), nullable=True))


def downgrade():
    op.drop_column("items", "reward")
