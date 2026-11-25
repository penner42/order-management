"""Share the existing return match ID across warehouse providers."""
from alembic import op

revision = "093"
down_revision = "092"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("items", "dnr_damaged_warehouse_id", new_column_name="receiving_return_id")


def downgrade():
    op.execute("UPDATE items SET receiving_return_id = NULL WHERE receiving_return_id LIKE 'buyinggroup:%'")
    op.alter_column("items", "receiving_return_id", new_column_name="dnr_damaged_warehouse_id")
