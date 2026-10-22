"""Store the warehouse product UPC separately from the purchased description."""
from alembic import op
import sqlalchemy as sa

revision = "079"
down_revision = "078"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("receipt_upc", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("items", "receipt_upc")
