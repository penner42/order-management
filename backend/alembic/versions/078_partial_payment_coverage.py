"""Allow installment coverage without changing existing payment amounts."""
from alembic import op
import sqlalchemy as sa

revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("payment_receipt_coverage") as batch:
        batch.drop_constraint("uq_receipt_coverage_item", type_="unique")
        batch.create_unique_constraint("uq_receipt_payment_coverage_item", ["receipt_source_id", "payment_id", "item_id"])


def downgrade():
    # Refuse to discard installment history when reverting the constraint.
    connection = op.get_bind()
    duplicates = connection.execute(sa.text("SELECT 1 FROM payment_receipt_coverage GROUP BY receipt_source_id, item_id HAVING COUNT(*) > 1 LIMIT 1")).first()
    if duplicates:
        raise RuntimeError("Cannot downgrade while receipts have installment coverage")
    with op.batch_alter_table("payment_receipt_coverage") as batch:
        batch.drop_constraint("uq_receipt_payment_coverage_item", type_="unique")
        batch.create_unique_constraint("uq_receipt_coverage_item", ["receipt_source_id", "item_id"])
