from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from app.config import AppConfig
from app.db import Base, configure_sqlite_connection, create_engine_for_app
from app.migrate import head_revision, upgrade_to_head
from app.models import Booking, WebSession
from sqlalchemy import create_engine, event, inspect, text
from tests.conftest import TEST_HASH


def test_upgrade_from_empty_database_reaches_head_and_matches_metadata(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config = AppConfig(
        secret_key="local-test-secret-key-32-bytes-min",
        access_code_hmac_secret="local-test-access-code-hmac-secret-32",
        admin_password_hash=TEST_HASH,
        data_dir=data_dir,
        session_cookie_secure=False,
        trust_proxy=False,
        require_data_mount=False,
        log_level="WARNING",
    )
    revision = upgrade_to_head(config.database_url)
    assert revision == head_revision()
    assert revision == "0003_bookings"
    assert config.database_path.is_file()

    engine = create_engine_for_app(config.database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
            assert diff == [], diff
        inspector = inspect(engine)
        assert inspector.has_table("web_sessions")
        columns = {column["name"] for column in inspector.get_columns("web_sessions")}
        assert columns == {
            "id",
            "session_digest",
            "actor_type",
            "booking_id",
            "created_at",
            "last_seen_at",
            "expires_at",
        }
        assert WebSession.__tablename__ == "web_sessions"
        assert inspector.has_table("bookings")
        booking_columns = {
            column["name"] for column in inspector.get_columns("bookings")
        }
        assert booking_columns == {
            "id",
            "public_reference",
            "access_code_digest",
            "event_date",
            "revision",
            "created_at",
            "updated_at",
        }
        booking_unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("bookings")
        }
        assert booking_unique_constraints == {
            "uq_bookings_access_code_digest",
            "uq_bookings_public_reference",
        }
        booking_foreign_key = next(
            foreign_key
            for foreign_key in inspector.get_foreign_keys("web_sessions")
            if foreign_key["constrained_columns"] == ["booking_id"]
        )
        assert booking_foreign_key["referred_table"] == "bookings"
        assert booking_foreign_key["options"]["ondelete"] == "CASCADE"
        with engine.connect() as connection:
            trigger_sql = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' "
                    "AND name = 'prevent_booking_reference_update'"
                )
            ).scalar_one()
        assert "BEFORE UPDATE OF public_reference" in trigger_sql
        assert Booking.__tablename__ == "bookings"
    finally:
        engine.dispose()


def test_sqlite_pragmas_apply_to_fresh_connections(tmp_path: Path) -> None:
    database = tmp_path / "demo.sqlite3"
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )
    event.listen(engine, "connect", configure_sqlite_connection)
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE example (id INTEGER PRIMARY KEY)"))
            connection.commit()
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    finally:
        engine.dispose()
