"""Persist BG rewards once per tracking and provider account."""
from alembic import op
import sqlalchemy as sa

revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tracking_rewards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("buying_group_id", sa.Integer(), sa.ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_identity", sa.String(64), nullable=False),
        sa.Column("tracking_key", sa.String(255), nullable=False),
        sa.Column("tracking_number", sa.String(255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("receipt_id", sa.String(255)),
        sa.UniqueConstraint("buying_group_id", "provider_identity", "tracking_key", name="uq_tracking_reward_account_key"))
    op.create_index("ix_tracking_rewards_buying_group_id", "tracking_rewards", ["buying_group_id"])


def downgrade():
    op.drop_table("tracking_rewards")
