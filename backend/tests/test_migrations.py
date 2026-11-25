"""The Alembic history must build the schema on its own, without create_all."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.database import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent
PREVIOUS_HEAD = "c0d1e2f3a4b5"


@pytest.fixture
def migration_db(tmp_path, monkeypatch):
    """A file-backed SQLite URL that alembic/env.py picks up from settings."""
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    # No ini file: env.py then skips fileConfig(), which would reset logging
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    engine = sa.create_engine(url)
    yield cfg, engine
    engine.dispose()


def _tables(engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _current(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def test_upgrade_head_from_empty_database(migration_db):
    cfg, engine = migration_db
    command.upgrade(cfg, "head")
    assert _tables(engine) == set(Base.metadata.tables)

    # Down across the mesh/security merge points and back up again
    command.downgrade(cfg, "e8f9a0b1c2d3")
    command.upgrade(cfg, "head")
    assert _tables(engine) == set(Base.metadata.tables)

    command.downgrade(cfg, "base")
    assert _tables(engine) == set()
    command.upgrade(cfg, "head")
    assert _tables(engine) == set(Base.metadata.tables)


def test_upgrade_existing_install_created_by_create_all(migration_db):
    cfg, engine = migration_db
    Base.metadata.create_all(bind=engine)
    command.stamp(cfg, PREVIOUS_HEAD)
    command.upgrade(cfg, "head")
    assert _current(engine) == ScriptDirectory.from_config(cfg).get_current_head()
    assert _tables(engine) == set(Base.metadata.tables)
