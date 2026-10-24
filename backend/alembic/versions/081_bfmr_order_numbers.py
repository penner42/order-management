"""Durable BFMR order registration and imported product identities."""
from alembic import op
import sqlalchemy as sa

revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("buying_groups", sa.Column("order_number_submit_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("orders", sa.Column("imported_from_store", sa.String(50)))
    op.add_column("items", sa.Column("import_line_key", sa.String(500)))
    op.add_column("items", sa.Column("external_sku", sa.String(255)))
    op.add_column("items", sa.Column("product_url", sa.Text()))
    op.create_table(
        "bfmr_order_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("buying_group_id", sa.Integer(), sa.ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_key", sa.String(500), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("allocations", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "buying_group_id", "product_key"),
    )
    for field in ("order_id", "buying_group_id", "status", "next_attempt_at"):
        op.create_index(f"ix_bfmr_order_tasks_{field}", "bfmr_order_tasks", [field])


def downgrade():
    op.drop_table("bfmr_order_tasks")
    for field in ("product_url", "external_sku", "import_line_key"):
        op.drop_column("items", field)
    op.drop_column("orders", "imported_from_store")
    op.drop_column("buying_groups", "order_number_submit_enabled")
