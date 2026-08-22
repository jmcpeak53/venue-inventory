from __future__ import annotations

from flask import Flask, g
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_engine_for_app(database_url: str) -> Engine:
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 5.0},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", configure_sqlite_connection)
    return engine


def init_db(app: Flask, engine: Engine) -> None:
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    app.extensions["engine"] = engine
    app.extensions["session_factory"] = factory

    @app.teardown_appcontext
    def close_session(_exc: BaseException | None) -> None:
        session = g.pop("db_session", None)
        if session is not None:
            session.close()


def get_session() -> Session:
    from flask import current_app

    if "db_session" not in g:
        g.db_session = current_app.extensions["session_factory"]()
    return g.db_session
