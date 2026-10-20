"""Add persisted, staggered daily and interval store checks."""
from datetime import datetime, timezone

from alembic import op
from croniter import croniter
import sqlalchemy as sa

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("browser_profiles", sa.Column("unshipped_check_interval_minutes", sa.Integer(), nullable=False, server_default="360"))
    for mode in ("full", "unshipped"):
        op.add_column("browser_profiles", sa.Column(f"{mode}_check_next_run_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_store_unshipped_interval", "browser_profiles", "unshipped_check_interval_minutes BETWEEN 1 AND 525600")
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, unshipped_check_cron FROM browser_profiles WHERE integration IS NULL")).mappings().all()
    for row in rows:
        try:
            cron = croniter(row["unshipped_check_cron"], datetime.now(timezone.utc))
            first = cron.get_next(datetime)
            minutes = max(1, min(525600, round((cron.get_next(datetime) - first).total_seconds() / 60)))
        except (ValueError, KeyError, TypeError):
            minutes = 360
        connection.execute(sa.text("UPDATE browser_profiles SET unshipped_check_interval_minutes=:minutes WHERE id=:id"), {"minutes": minutes, "id": row["id"]})
    # The next scheduler tick initializes the stagger without an immediate burst.


def downgrade():
    op.drop_constraint("ck_store_unshipped_interval", "browser_profiles", type_="check")
    for mode in ("full", "unshipped"):
        op.drop_column("browser_profiles", f"{mode}_check_next_run_at")
    op.drop_column("browser_profiles", "unshipped_check_interval_minutes")
