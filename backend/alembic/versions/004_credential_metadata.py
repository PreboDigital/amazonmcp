"""Add credentials.credential_metadata JSON column.

Revision ID: 004
Revises: 003
Create Date: 2026-04-29

Adds a generic JSON column on ``credentials`` so we can persist runtime
state (e.g. report-date skip lists used by ``report_skip_service``)
without piling on dedicated tables for one-off counters.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("credentials")}
    if "credential_metadata" in cols:
        return
    op.add_column(
        "credentials",
        sa.Column(
            "credential_metadata",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("credentials", "credential_metadata")
