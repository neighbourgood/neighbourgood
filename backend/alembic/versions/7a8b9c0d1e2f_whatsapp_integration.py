"""WhatsApp integration: user WhatsApp id, light accounts, community group link and digest

Revision ID: 7a8b9c0d1e2f
Revises: c0d1e2f3a4b5
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '7a8b9c0d1e2f'
down_revision: Union[str, Sequence[str], None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The startup auto-add may already have created the plain columns on
    # Docker installs; only add what is missing, then the constraints/indexes.
    insp = sa.inspect(op.get_bind())
    user_cols = {c["name"] for c in insp.get_columns("users")}
    community_cols = {c["name"] for c in insp.get_columns("communities")}

    with op.batch_alter_table("users") as batch:
        if "whatsapp_id" not in user_cols:
            batch.add_column(sa.Column("whatsapp_id", sa.String(64), nullable=True))
        if "whatsapp_notify" not in user_cols:
            batch.add_column(sa.Column("whatsapp_notify", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "whatsapp_dm_at" not in user_cols:
            batch.add_column(sa.Column("whatsapp_dm_at", sa.DateTime(), nullable=True))
        if "account_type" not in user_cols:
            batch.add_column(sa.Column("account_type", sa.String(20), nullable=False, server_default="full"))
    op.create_index("ix_users_whatsapp_id", "users", ["whatsapp_id"], unique=True)

    with op.batch_alter_table("communities") as batch:
        if "whatsapp_group_id" not in community_cols:
            batch.add_column(sa.Column("whatsapp_group_id", sa.String(64), nullable=True))
        if "whatsapp_digest" not in community_cols:
            batch.add_column(sa.Column("whatsapp_digest", sa.String(10), nullable=False, server_default="weekly"))
        if "whatsapp_last_digest_at" not in community_cols:
            batch.add_column(sa.Column("whatsapp_last_digest_at", sa.DateTime(), nullable=True))
    op.create_index("ix_communities_whatsapp_group_id", "communities", ["whatsapp_group_id"])


def downgrade() -> None:
    op.drop_index("ix_communities_whatsapp_group_id", table_name="communities")
    with op.batch_alter_table("communities") as batch:
        batch.drop_column("whatsapp_last_digest_at")
        batch.drop_column("whatsapp_digest")
        batch.drop_column("whatsapp_group_id")
    op.drop_index("ix_users_whatsapp_id", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("account_type")
        batch.drop_column("whatsapp_dm_at")
        batch.drop_column("whatsapp_notify")
        batch.drop_column("whatsapp_id")
