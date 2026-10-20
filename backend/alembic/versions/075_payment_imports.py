"""Provider-neutral payment review and receipt corrections."""
from alembic import op
import sqlalchemy as sa

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("payment_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("buying_group_id", sa.Integer(), sa.ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("buying_group_id", "provider", "kind", "external_id", name="uq_payment_source_identity"))
    op.create_index("ix_payment_sources_buying_group_id", "payment_sources", ["buying_group_id"])
    op.create_table("payment_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("payment_sources.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), unique=True),
        sa.Column("confirmed_revision", sa.String(64)))
    op.create_table("payment_receipt_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_source_id", sa.Integer(), sa.ForeignKey("payment_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint("receipt_source_id", "item_id", name="uq_receipt_coverage_item"))
    op.create_table("payment_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_source_id", sa.Integer(), sa.ForeignKey("payment_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("explanation", sa.String(2000), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False))
    for table in ("payment_receipt_coverage", "payment_corrections"):
        for column in ("receipt_source_id", "payment_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ("payment_corrections", "payment_receipt_coverage", "payment_reviews", "payment_sources"):
        op.drop_table(table)
