"""cleanup duplicate active community memberships

Enforces "one active community per user" retroactively: for every user
who has more than one CommunityMember row in an active, non-merged
community, keep the oldest (earliest joined_at, ties broken by lowest id)
and delete the rest. Memberships in inactive/merged-away communities
(stale rows left behind by community merges) are left untouched — they
are not counted and not deleted. Idempotent: a second run is a no-op.

Revision ID: b6cf30bac69d
Revises: c7d8e9f0a1b2
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6cf30bac69d'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


community_members = sa.table(
    'community_members',
    sa.column('id', sa.Integer),
    sa.column('community_id', sa.Integer),
    sa.column('user_id', sa.Integer),
    sa.column('joined_at', sa.DateTime),
)
communities = sa.table(
    'communities',
    sa.column('id', sa.Integer),
    sa.column('is_active', sa.Boolean),
    sa.column('merged_into_id', sa.Integer),
)


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.select(
            community_members.c.id,
            community_members.c.user_id,
        )
        .select_from(
            community_members.join(
                communities, community_members.c.community_id == communities.c.id
            )
        )
        .where(communities.c.is_active.is_(True))
        .where(communities.c.merged_into_id.is_(None))
        .order_by(
            community_members.c.user_id,
            community_members.c.joined_at.asc(),
            community_members.c.id.asc(),
        )
    ).fetchall()

    seen_users: set[int] = set()
    ids_to_delete: list[int] = []
    for row in rows:
        if row.user_id in seen_users:
            ids_to_delete.append(row.id)
        else:
            seen_users.add(row.user_id)

    if ids_to_delete:
        bind.execute(
            community_members.delete().where(community_members.c.id.in_(ids_to_delete))
        )


def downgrade() -> None:
    # Irreversible data cleanup — the removed duplicate rows cannot be
    # reconstructed. No-op, consistent with other data-only migrations.
    pass
