from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from app.db import configure_sqlite_connection

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def upgrade_to_head(database_url: str) -> str:
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "head")
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )
    event.listen(engine, "connect", configure_sqlite_connection)
    try:
        revision = current_revision(engine)
    finally:
        engine.dispose()
    if revision is None:
        raise RuntimeError("Migration completed but no schema revision was recorded.")
    return revision


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return context.get_current_revision()


def head_revision() -> str:
    cfg = alembic_config("sqlite://")
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("No Alembic head revision is configured.")
    return head
