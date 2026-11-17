"""Retain cropped screenshots of retailer order changes."""
from alembic import op
import sqlalchemy as sa

revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_screenshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(64), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
    )
    op.create_index("ix_order_screenshots_order_id", "order_screenshots", ["order_id"])


def downgrade():
    op.drop_table("order_screenshots")
