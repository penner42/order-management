"""Save product barcodes supplied by store imports separately from receipt UPCs."""
from alembic import op
import sqlalchemy as sa

revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("upc", sa.String(14), nullable=True))


def downgrade():
    op.drop_column("items", "upc")
