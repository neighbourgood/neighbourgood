"""create tables that only Base.metadata.create_all() ever made

mesh_checkins (v1.9.5) and the federation sync tables instance_sync_logs,
federated_resources and federated_skills (v1.4.0) never had a migration, so
`alembic upgrade head` on an empty database left them out. Each table is
created only when missing, so existing installs (where the startup
create_all already made them) just record the new revision.

Revision ID: d4e5f6a7b8c9
Revises: 7a8b9c0d1e2f
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = '7a8b9c0d1e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table('mesh_checkins'):
        op.create_table(
            'mesh_checkins',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('community_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('lat', sa.Float(), nullable=False),
            sa.Column('lng', sa.Float(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('checked_in_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.ForeignKeyConstraint(['community_id'], ['communities.id']),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('mesh_checkins', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_mesh_checkins_community_id'), ['community_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_mesh_checkins_user_id'), ['user_id'], unique=False)

    if not _has_table('instance_sync_logs'):
        op.create_table(
            'instance_sync_logs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('instance_id', sa.Integer(), nullable=False),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('resources_synced', sa.Integer(), nullable=False),
            sa.Column('skills_synced', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('error_message', sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(['instance_id'], ['known_instances.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('instance_sync_logs', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_instance_sync_logs_instance_id'), ['instance_id'], unique=False)

    if not _has_table('federated_resources'):
        op.create_table(
            'federated_resources',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('source_instance_id', sa.Integer(), nullable=False),
            sa.Column('remote_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=False),
            sa.Column('condition', sa.String(length=100), nullable=False),
            sa.Column('community_name', sa.String(length=200), nullable=False),
            sa.Column('owner_display_name', sa.String(length=100), nullable=False),
            sa.Column('is_available', sa.Boolean(), nullable=False),
            sa.Column('remote_created_at', sa.DateTime(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.ForeignKeyConstraint(['source_instance_id'], ['known_instances.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('source_instance_id', 'remote_id', name='uq_fedres_instance_remote'),
        )
        with op.batch_alter_table('federated_resources', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_federated_resources_source_instance_id'), ['source_instance_id'], unique=False)

    if not _has_table('federated_skills'):
        op.create_table(
            'federated_skills',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('source_instance_id', sa.Integer(), nullable=False),
            sa.Column('remote_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=False),
            sa.Column('skill_type', sa.String(length=20), nullable=False),
            sa.Column('community_name', sa.String(length=200), nullable=False),
            sa.Column('owner_display_name', sa.String(length=100), nullable=False),
            sa.Column('remote_created_at', sa.DateTime(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.ForeignKeyConstraint(['source_instance_id'], ['known_instances.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('source_instance_id', 'remote_id', name='uq_fedskill_instance_remote'),
        )
        with op.batch_alter_table('federated_skills', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_federated_skills_source_instance_id'), ['source_instance_id'], unique=False)


def downgrade() -> None:
    for table in ('federated_skills', 'federated_resources', 'instance_sync_logs', 'mesh_checkins'):
        if _has_table(table):
            op.drop_table(table)
