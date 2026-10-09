"""Replace interval-hours schedules with cron expressions.

Revision ID: 055
Revises: 054
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _hours_to_cron(hours: int) -> str:
    h = max(1, min(int(hours or 1), 720))
    if h % 24 == 0:
        days = h // 24
        return "0 0 * * *" if days == 1 else f"0 0 */{days} * *"
    return f"0 */{h} * * *"


def upgrade() -> None:
    op.add_column(
        "browser_profiles",
        sa.Column(
            "full_check_cron",
            sa.String(64),
            nullable=False,
            server_default="0 0 * * *",
        ),
    )
    op.add_column(
        "browser_profiles",
        sa.Column(
            "unshipped_check_cron",
            sa.String(64),
            nullable=False,
            server_default="0 */6 * * *",
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, full_check_interval_hours, unshipped_check_interval_hours "
            "FROM browser_profiles"
        )
    ).fetchall()
    for row in rows:
        conn.execute(
            sa.text(
                "UPDATE browser_profiles "
                "SET full_check_cron = :full_cron, unshipped_check_cron = :unshipped_cron "
                "WHERE id = :id"
            ),
            {
                "id": row[0],
                "full_cron": _hours_to_cron(row[1]),
                "unshipped_cron": _hours_to_cron(row[2]),
            },
        )

    op.drop_column("browser_profiles", "full_check_interval_hours")
    op.drop_column("browser_profiles", "unshipped_check_interval_hours")


def downgrade() -> None:
    op.add_column(
        "browser_profiles",
        sa.Column(
            "full_check_interval_hours",
            sa.Integer(),
            nullable=False,
            server_default="24",
        ),
    )
    op.add_column(
        "browser_profiles",
        sa.Column(
            "unshipped_check_interval_hours",
            sa.Integer(),
            nullable=False,
            server_default="6",
        ),
    )
    op.drop_column("browser_profiles", "unshipped_check_cron")
    op.drop_column("browser_profiles", "full_check_cron")
