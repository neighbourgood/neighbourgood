"""add inventory fields to resources and triage fields to emergency_tickets

Revision ID: a1b2c3d4e5f6
Revises: 50da5249c4f2
Create Date: 2026-02-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '50da5249c4f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return _has_table(table) and any(
        c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table)
    )


def upgrade() -> None:
    # ── Resource inventory fields ──────────────────────────────────
    with op.batch_alter_table('resources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quantity_total', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('quantity_available', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('reorder_threshold', sa.Integer(), nullable=True))

    # ── EmergencyTicket triage field ───────────────────────────────
    # emergency_tickets was first created by Base.metadata.create_all() at
    # startup, never by a migration before this one. On an empty database,
    # create it here (with due_at) so the history works without create_all;
    # 6ee90969736f then skips it.
    if not _has_table('emergency_tickets'):
        op.create_table('emergency_tickets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('community_id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('ticket_type', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('urgency', sa.String(length=20), nullable=False),
        sa.Column('assigned_to_id', sa.Integer(), nullable=True),
        sa.Column('due_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['community_id'], ['communities.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('emergency_tickets', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_emergency_tickets_author_id'), ['author_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_emergency_tickets_community_id'), ['community_id'], unique=False)
    elif not _has_column('emergency_tickets', 'due_at'):
        with op.batch_alter_table('emergency_tickets', schema=None) as batch_op:
            batch_op.add_column(sa.Column('due_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # The table itself may predate this revision (create_all), so only the
    # column is removed; 6ee90969736f's downgrade drops the table.
    if _has_column('emergency_tickets', 'due_at'):
        with op.batch_alter_table('emergency_tickets', schema=None) as batch_op:
            batch_op.drop_column('due_at')

    with op.batch_alter_table('resources', schema=None) as batch_op:
        batch_op.drop_column('reorder_threshold')
        batch_op.drop_column('quantity_available')
        batch_op.drop_column('quantity_total')
