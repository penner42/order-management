"""Replace buying group cron scheduling with staggered intervals."""
from datetime import datetime, timezone

from alembic import op
from croniter import croniter
import sqlalchemy as sa

revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def upgrade():
    for kind in ("schedule",):
        op.add_column("buying_groups", sa.Column(f"{kind}_interval_minutes", sa.Integer(), nullable=False, server_default="360"))
        op.add_column("buying_groups", sa.Column(f"{kind}_next_run_at", sa.DateTime(timezone=True)))
        op.create_check_constraint(f"ck_{kind}_interval", "buying_groups", f"{kind}_interval_minutes BETWEEN 1 AND 525600")
    connection = op.get_bind()
    groups = connection.execute(sa.text("SELECT id, tracking_submit_cron, received_check_cron FROM buying_groups")).mappings().all()
    for group in groups:
        # Receiving previously followed submission, so preserve its effective cadence.
        try:
            cron = croniter(group["tracking_submit_cron"], datetime.now(timezone.utc))
            first = cron.get_next(datetime)
            minutes = max(1, min(525600, round((cron.get_next(datetime) - first).total_seconds() / 60)))
        except (ValueError, KeyError, TypeError):
            minutes = 360
        connection.execute(sa.text("UPDATE buying_groups SET schedule_interval_minutes=:minutes, received_check_enabled=tracking_submit_enabled WHERE id=:id"), {"minutes": minutes, "id": group["id"]})
    # The scheduler initializes and persists a fresh stagger on its next tick.


def downgrade():
    for kind in ("schedule",):
        op.drop_constraint(f"ck_{kind}_interval", "buying_groups", type_="check")
        op.drop_column("buying_groups", f"{kind}_next_run_at")
        op.drop_column("buying_groups", f"{kind}_interval_minutes")
