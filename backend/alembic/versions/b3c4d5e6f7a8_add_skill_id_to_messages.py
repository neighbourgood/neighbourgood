"""add skill_id to messages

Revision ID: b3c4d5e6f7a8
Revises: 72f9e65d40b8
Create Date: 2026-02-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = '72f9e65d40b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    return any(c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def _has_fk(table: str, name: str) -> bool:
    return any(fk.get('name') == name for fk in sa.inspect(op.get_bind()).get_foreign_keys(table))


def upgrade() -> None:
    # On an empty database the skills table does not exist yet (it was first
    # created by Base.metadata.create_all(); 6ee90969736f creates it in the
    # history). Postgres refuses a foreign key to a missing table, so the
    # constraint is added here only when skills exists, else by 6ee90969736f.
    has_skills = sa.inspect(op.get_bind()).has_table('skills')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('skill_id', sa.Integer(), nullable=True))
        if has_skills:
            batch_op.create_foreign_key('fk_messages_skill_id', 'skills', ['skill_id'], ['id'])
        batch_op.create_index(batch_op.f('ix_messages_skill_id'), ['skill_id'], unique=False)


def downgrade() -> None:
    # 6ee90969736f's downgrade may already have removed the column
    if not _has_column('messages', 'skill_id'):
        return
    has_fk = _has_fk('messages', 'fk_messages_skill_id')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_skill_id'))
        if has_fk:
            batch_op.drop_constraint('fk_messages_skill_id', type_='foreignkey')
        batch_op.drop_column('skill_id')
