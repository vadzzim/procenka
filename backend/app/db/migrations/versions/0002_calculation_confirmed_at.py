"""Confirmation of a quarantined recommendation: calculations.confirmed_at.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calculations", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_calculations_confirmed_only_quarantine"),
        "calculations",
        "confirmed_at IS NULL OR state = 'quarantine'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_calculations_confirmed_only_quarantine"), "calculations", type_="check"
    )
    op.drop_column("calculations", "confirmed_at")
