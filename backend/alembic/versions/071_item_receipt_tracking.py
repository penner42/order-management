"""Store warehouse receipt tracking on individual order lines."""
from alembic import op
import sqlalchemy as sa

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("receipt_tracking_number", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "receipt_tracking_number")
