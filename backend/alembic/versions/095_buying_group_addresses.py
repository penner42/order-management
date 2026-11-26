"""Store shipping addresses and saved variants for buying groups."""
import sqlalchemy as sa
from alembic import op

revision = "095"
down_revision = "094"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "buying_group_addresses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("buying_group_id", sa.Integer, sa.ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
    )
    op.create_index("ix_buying_group_addresses_buying_group_id", "buying_group_addresses", ["buying_group_id"])
    op.create_table(
        "buying_group_address_variants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("address_id", sa.Integer, sa.ForeignKey("buying_group_addresses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
    )
    op.create_index("ix_buying_group_address_variants_address_id", "buying_group_address_variants", ["address_id"])


def downgrade():
    op.drop_table("buying_group_address_variants")
    op.drop_table("buying_group_addresses")
