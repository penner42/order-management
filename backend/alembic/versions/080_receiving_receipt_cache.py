"""Keep complete receiving inputs independently of the review queue."""
from alembic import op
import sqlalchemy as sa

revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receiving_receipt_cache",
        sa.Column("buying_group_id", sa.Integer(), sa.ForeignKey("buying_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("provider_identity", sa.String(1024), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("receiving_receipt_cache")
