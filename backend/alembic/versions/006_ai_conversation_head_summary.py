"""Add ai_conversations.head_summary column.

Revision ID: 006
Revises: 005
Create Date: 2026-04-29

Backs the rolling-summary memory pattern used by
``app.services.ai_memory.compact_if_needed``: when the conversation
exceeds the message / char budget, the oldest turns are summarised
into ``head_summary`` and dropped from ``messages``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("ai_conversations")}
    if "head_summary" not in cols:
        op.add_column(
            "ai_conversations",
            sa.Column("head_summary", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("ai_conversations", "head_summary")
