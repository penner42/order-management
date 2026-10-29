"""Keep cached receiving replays from replacing newer tracking reward evidence."""
from alembic import op
import sqlalchemy as sa

revision = "085"
down_revision = "084"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tracking_rewards", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("tracking_rewards", "observed_at")
