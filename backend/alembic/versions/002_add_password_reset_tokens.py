"""Add password_reset_tokens table for forgot password flow.

Revision ID: 002
Revises: 001
Create Date: 2025-02-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "password_reset_tokens" in insp.get_table_names():
        return

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_reset_tokens_token", "password_reset_tokens", ["token"], unique=True)
    op.create_index("ix_password_reset_tokens_email", "password_reset_tokens", ["email"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_email", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
