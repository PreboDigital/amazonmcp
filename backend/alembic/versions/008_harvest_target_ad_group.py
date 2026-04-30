"""Harvest target ad group selection.

Amazon Sponsored Products keywords/targets live on an ad group, not a
campaign. The harvester previously stored only the target *campaign*
and asked Amazon for the first ad group at run-time, which silently
dropped keywords into the wrong group when the manual campaign held
multiple ad groups (or a product-targeting one).

Adds two nullable columns to ``harvest_configs`` so the user can pin
the exact ad group at config time:

* ``target_ad_group_id`` — Amazon ad group id (``adGroupId``).
* ``target_ad_group_name`` — display name cached for UI.

Both stay nullable because ``target_mode = 'new'`` does not need them.

Revision ID: 008
Revises: 007
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing = {c["name"] for c in insp.get_columns("harvest_configs")}
    if "target_ad_group_id" not in existing:
        op.add_column(
            "harvest_configs",
            sa.Column("target_ad_group_id", sa.String(255), nullable=True),
        )
    if "target_ad_group_name" not in existing:
        op.add_column(
            "harvest_configs",
            sa.Column("target_ad_group_name", sa.String(255), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing = {c["name"] for c in insp.get_columns("harvest_configs")}
    if "target_ad_group_name" in existing:
        op.drop_column("harvest_configs", "target_ad_group_name")
    if "target_ad_group_id" in existing:
        op.drop_column("harvest_configs", "target_ad_group_id")
