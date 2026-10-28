"""Source-owned payment totals and editable allocation ledger."""
from alembic import op
import sqlalchemy as sa

revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("payments") as b:
        b.add_column(sa.Column("source_amount", sa.Numeric(12, 2), nullable=True))
        b.add_column(sa.Column("ledger_state", sa.String(16), nullable=False, server_default="legacy"))
        b.add_column(sa.Column("allocation_manual", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("allocation_version", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("allocation_details", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("sync_issues", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("payment_sources") as b:
        b.add_column(sa.Column("revisions", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("buying_groups") as b:
        b.add_column(sa.Column("payment_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("payment_sync_last_run_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("payment_sync_error", sa.String(2000)))
    # Preserve already confirmed coverage as a manual baseline, including its audit rows.
    op.execute("""UPDATE payments SET source_amount = CAST((SELECT s.payload ->> 'amount'
        FROM payment_reviews r JOIN payment_sources s ON s.id = r.source_id
        WHERE r.payment_id = payments.id) AS NUMERIC(12,2)),
        ledger_state = 'imported', allocation_manual = true
        WHERE id IN (SELECT payment_id FROM payment_reviews WHERE payment_id IS NOT NULL)""")


def downgrade():
    raise RuntimeError("Source payment ledger history must be preserved; restore a backup to downgrade")
