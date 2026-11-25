"""Store the D&R warehouse unit matched to a return."""
from alembic import op
import sqlalchemy as sa

revision = "092"
down_revision = "091"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("dnr_damaged_warehouse_id", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_items_dnr_damaged_warehouse_id", "items", ["dnr_damaged_warehouse_id"])


def downgrade():
    op.drop_constraint("uq_items_dnr_damaged_warehouse_id", "items", type_="unique")
    op.drop_column("items", "dnr_damaged_warehouse_id")
