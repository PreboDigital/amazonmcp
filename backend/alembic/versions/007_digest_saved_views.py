"""Weekly digest preference + saved views.

Revision ID: 007
Revises: 006
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "weekly_digest_enabled" not in user_cols:
        op.add_column(
            "users",
            sa.Column("weekly_digest_enabled", sa.Boolean(), nullable=False, server_default="true"),
        )
        op.alter_column("users", "weekly_digest_enabled", server_default=None)

    if not insp.has_table("saved_views"):
        op.create_table(
            "saved_views",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("page", sa.String(64), nullable=False),
            sa.Column("credential_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True),
            sa.Column("profile_id", sa.String(255), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        )
        op.create_index("ix_saved_views_user_id", "saved_views", ["user_id"])
        op.create_index("ix_saved_views_page", "saved_views", ["page"])


def downgrade() -> None:
    op.drop_index("ix_saved_views_page", table_name="saved_views", if_exists=True)
    op.drop_index("ix_saved_views_user_id", table_name="saved_views", if_exists=True)
    op.drop_table("saved_views")
    op.drop_column("users", "weekly_digest_enabled")
