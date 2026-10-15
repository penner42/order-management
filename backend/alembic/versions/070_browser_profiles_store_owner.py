"""Allow browser profiles on stores as well as store accounts.

Revision ID: 070
Revises: 069
"""
from alembic import op
import sqlalchemy as sa

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browser_profiles", sa.Column("store_id", sa.Integer(), nullable=True))
    op.execute("UPDATE browser_profiles p SET store_id = a.store_id FROM store_accounts a WHERE a.id = p.store_account_id")
    op.alter_column("browser_profiles", "store_id", nullable=False)
    op.create_foreign_key("fk_browser_profiles_store_id", "browser_profiles", "stores", ["store_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_browser_profiles_store_id", "browser_profiles", ["store_id"])
    op.alter_column("browser_profiles", "store_account_id", nullable=True)
    op.create_index("uq_browser_profiles_direct_store", "browser_profiles", ["store_id"], unique=True,
                    postgresql_where=sa.text("store_account_id IS NULL"))


def downgrade() -> None:
    # Refuse a lossy downgrade: direct store profiles must be explicitly moved first.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM browser_profiles WHERE store_account_id IS NULL")):
        raise RuntimeError("Move store-level profiles to accounts before downgrading.")
    op.drop_index("uq_browser_profiles_direct_store", table_name="browser_profiles")
    op.alter_column("browser_profiles", "store_account_id", nullable=False)
    op.drop_index("ix_browser_profiles_store_id", table_name="browser_profiles")
    op.drop_constraint("fk_browser_profiles_store_id", "browser_profiles", type_="foreignkey")
    op.drop_column("browser_profiles", "store_id")
