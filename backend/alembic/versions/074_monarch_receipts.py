"""Add Monarch integration ownership and payment receipt evidence."""
from alembic import op
import sqlalchemy as sa

revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("browser_profiles", "store_id", nullable=True)
    op.add_column("browser_profiles", sa.Column("integration", sa.String(64)))
    op.create_unique_constraint("uq_browser_profiles_integration", "browser_profiles", ["integration"])
    op.create_check_constraint("ck_browser_profile_owner", "browser_profiles",
        "(integration IS NOT NULL AND integration = 'monarch' AND retailer = 'monarch' AND store_id IS NULL AND store_account_id IS NULL) OR "
        "(integration IS NULL AND store_id IS NOT NULL AND retailer <> 'monarch')")
    op.create_table("monarch_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("browser_profile_id", sa.Integer(), sa.ForeignKey("browser_profiles.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("selected_account_ids", sa.JSON(), nullable=False),
        sa.Column("sender_rules", sa.JSON(), nullable=False),
        sa.Column("accounts", sa.JSON(), nullable=False),
        sa.Column("daily_enabled", sa.Boolean(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_monarch_singleton"))
    op.create_table("monarch_deposits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.String(255), nullable=False, unique=True),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("merchant", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_monarch_deposits_account_id", "monarch_deposits", ["account_id"])
    op.create_table("monarch_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deposit_id", sa.Integer(), sa.ForeignKey("monarch_deposits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("active_deposit_id", sa.Integer(), sa.ForeignKey("monarch_deposits.id", ondelete="RESTRICT"), unique=True),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("undone_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.create_table("monarch_payment_receipts",
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("confirmation_id", sa.Integer(), sa.ForeignKey("monarch_confirmations.id", ondelete="RESTRICT"), nullable=False))


def downgrade():
    # Preserve audit records unless the operator explicitly removes the connection/evidence.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM monarch_confirmations")):
        raise RuntimeError("Export and remove Monarch receipt evidence before downgrading.")
    for table in ("monarch_payment_receipts", "monarch_confirmations", "monarch_deposits", "monarch_connections"):
        op.drop_table(table)
    op.execute("DELETE FROM browser_profiles WHERE integration = 'monarch'")
    op.drop_constraint("ck_browser_profile_owner", "browser_profiles", type_="check")
    op.drop_constraint("uq_browser_profiles_integration", "browser_profiles", type_="unique")
    op.drop_column("browser_profiles", "integration")
    op.alter_column("browser_profiles", "store_id", nullable=False)
