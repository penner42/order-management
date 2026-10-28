"""Restore the payment bonus default expected by payment imports."""
from alembic import op
import sqlalchemy as sa

revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("payments") as batch:
        batch.alter_column("payment_bonus", existing_type=sa.Numeric(12, 2),
                           existing_nullable=False, server_default=sa.text("0"))


def downgrade():
    with op.batch_alter_table("payments") as batch:
        batch.alter_column("payment_bonus", existing_type=sa.Numeric(12, 2),
                           existing_nullable=False, server_default=None)
