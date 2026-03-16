"""Backfill Walmart shipment tracking numbers to use order id and note original.

Revision ID: 045
Revises: 044
Create Date: 2026-03-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_walmart_tracking(value: str) -> bool:
    """Return True if value looks like a Walmart 20-digit 555… tracking number."""
    v = (value or "").replace(" ", "")
    return len(v) == 20 and v.startswith("555") and v.isdigit()


def upgrade() -> None:
    bind = op.get_bind()

    # We need: Walmart store id, orders with store_order_number, and their shipments.
    # Join shipments -> shipment_items -> items -> orders -> stores.
    query = text(
        """
        SELECT
            sh.id AS shipment_id,
            sh.tracking_number AS tracking_number,
            sh.notes AS notes,
            o.store_order_number AS store_order_number,
            s.name AS store_name
        FROM shipments sh
        JOIN shipment_items si ON si.shipment_id = sh.id
        JOIN items i ON i.id = si.item_id
        JOIN orders o ON o.id = i.order_id
        JOIN stores s ON s.id = o.store_id
        WHERE
            sh.tracking_number IS NOT NULL
            AND o.store_order_number IS NOT NULL
            AND LOWER(s.name) = 'walmart'
        """
    )

    # Use mappings() so we can access columns by name across SQLAlchemy versions.
    results = list(bind.execute(query).mappings())
    updates = []

    for row in results:
        shipment_id = row["shipment_id"]
        tracking_number = row["tracking_number"] or ""
        notes = row["notes"] or ""
        store_order_number = row["store_order_number"]

        if not tracking_number or not store_order_number:
            continue

        compact = tracking_number.replace(" ", "")
        if not _is_walmart_tracking(compact):
            continue

        walmart_original = compact
        new_tracking = store_order_number

        # Build new notes, appending the Walmart tracking line if not already present.
        existing_lines = [ln for ln in notes.splitlines() if ln.strip()] if notes else []
        note_line = f"Walmart tracking: {walmart_original}"
        if note_line not in existing_lines:
            existing_lines.append(note_line)
        new_notes = "\n".join(existing_lines) if existing_lines else None

        updates.append(
            {
                "shipment_id": shipment_id,
                "tracking_number": new_tracking,
                "notes": new_notes,
            }
        )

    if not updates:
        return

    update_stmt = text(
        """
        UPDATE shipments
        SET tracking_number = :tracking_number,
            notes = :notes
        WHERE id = :shipment_id
        """
    )

    for payload in updates:
        # SQLAlchemy Core connection.execute(text_stmt, params_dict)
        # expects the parameters as a single dictionary, not kwargs.
        bind.execute(update_stmt, payload)


def downgrade() -> None:
    # This backfill is not easily reversible: we cannot recover the original
    # Walmart tracking reliably once overwritten, so we leave data as-is.
    pass

