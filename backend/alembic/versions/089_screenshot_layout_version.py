"""Refresh screenshot framing without adding artificial order-change versions."""
from alembic import op
import sqlalchemy as sa

revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("order_screenshots", sa.Column("layout_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    op.drop_column("order_screenshots", "layout_version")
