"""Add search_term_performance.time_unit + backfill from legacy SUMMARY date.

Revision ID: 005
Revises: 004
Create Date: 2026-04-29

Background
----------

Historically the ``date`` column held both real ISO dates and the
sentinel literal ``"SUMMARY"`` for range-aggregate rows. Every reader
had to guard against that mixed-type column. This migration introduces
an explicit ``time_unit`` column (``DAILY`` | ``SUMMARY``) and backfills
existing rows so future code can filter by grain instead of by string
match on a date column.

We do **not** rewrite the date column itself in this migration — legacy
SUMMARY-as-date rows still read fine — to keep the upgrade additive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("search_term_performance")}
    if "time_unit" not in cols:
        op.add_column(
            "search_term_performance",
            sa.Column("time_unit", sa.String(20), nullable=True),
        )

    indexes = {ix["name"] for ix in insp.get_indexes("search_term_performance")}
    if "ix_stp_time_unit" not in indexes:
        op.create_index(
            "ix_stp_time_unit", "search_term_performance", ["time_unit"]
        )

    op.execute(
        """
        UPDATE search_term_performance
           SET time_unit = CASE
                WHEN time_unit IS NOT NULL THEN time_unit
                WHEN date = 'SUMMARY' THEN 'SUMMARY'
                ELSE 'DAILY'
            END
         WHERE time_unit IS NULL OR time_unit = ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_stp_time_unit", table_name="search_term_performance")
    op.drop_column("search_term_performance", "time_unit")
