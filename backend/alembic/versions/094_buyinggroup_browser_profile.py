"""Allow a persistent BuyingGroup.com browser profile per buying group."""
from alembic import op
import sqlalchemy as sa

revision = "094"
down_revision = "093"
branch_labels = None
depends_on = None

ORIGINAL_OWNER = (
    "(integration IS NOT NULL AND integration = 'monarch' AND retailer = 'monarch' "
    "AND store_id IS NULL AND store_account_id IS NULL) OR "
    "(integration IS NULL AND store_id IS NOT NULL AND retailer <> 'monarch')"
)


def upgrade():
    op.add_column("browser_profiles", sa.Column("buying_group_id", sa.Integer()))
    op.create_foreign_key("fk_browser_profiles_buying_group", "browser_profiles", "buying_groups",
                          ["buying_group_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_browser_profiles_buying_group", "browser_profiles", ["buying_group_id"])
    op.drop_constraint("ck_browser_profile_owner", "browser_profiles", type_="check")
    op.create_check_constraint("ck_browser_profile_owner", "browser_profiles",
        f"(buying_group_id IS NULL AND ({ORIGINAL_OWNER})) OR "
        "(buying_group_id IS NOT NULL AND integration IS NOT NULL AND retailer = 'buyinggroup' "
        "AND store_id IS NULL AND store_account_id IS NULL)")


def downgrade():
    op.execute("DELETE FROM browser_profiles WHERE buying_group_id IS NOT NULL")
    op.drop_constraint("ck_browser_profile_owner", "browser_profiles", type_="check")
    op.drop_constraint("uq_browser_profiles_buying_group", "browser_profiles", type_="unique")
    op.drop_constraint("fk_browser_profiles_buying_group", "browser_profiles", type_="foreignkey")
    op.drop_column("browser_profiles", "buying_group_id")
    op.create_check_constraint("ck_browser_profile_owner", "browser_profiles", ORIGINAL_OWNER)
