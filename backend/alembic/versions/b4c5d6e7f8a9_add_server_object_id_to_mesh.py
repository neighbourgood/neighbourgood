"""add server_object_id to mesh_synced_messages

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-03-16 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # mesh_synced_messages (v1.2.0) was only ever created by
    # Base.metadata.create_all(). On an empty database, create it here with
    # its original shape so this and later revisions (f3a4b5c6d7e8) apply.
    if not sa.inspect(op.get_bind()).has_table('mesh_synced_messages'):
        op.create_table(
            'mesh_synced_messages',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('mesh_message_id', sa.String(length=100), nullable=False),
            sa.Column('message_type', sa.String(length=30), nullable=False),
            sa.Column('community_id', sa.Integer(), nullable=False),
            sa.Column('synced_by_id', sa.Integer(), nullable=False),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.ForeignKeyConstraint(['community_id'], ['communities.id']),
            sa.ForeignKeyConstraint(['synced_by_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('mesh_synced_messages', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_mesh_synced_messages_mesh_message_id'), ['mesh_message_id'], unique=True)
            batch_op.create_index(batch_op.f('ix_mesh_synced_messages_community_id'), ['community_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_mesh_synced_messages_synced_by_id'), ['synced_by_id'], unique=False)

    with op.batch_alter_table('mesh_synced_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('server_object_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    # The table enters the history here, so going below this revision drops
    # it (otherwise its foreign keys block dropping communities/users).
    op.drop_table('mesh_synced_messages')
