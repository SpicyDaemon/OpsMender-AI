from __future__ import annotations

from sqlalchemy import create_engine

from backend.db.models import Base
from scripts.dev_server import _patch_sqlite_dev_schema


def _column_names(conn, table_name: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()
    return {row[1] for row in rows}


class TestPatchSqliteDevSchema:
    def test_adds_missing_nullable_columns_for_existing_sessions_table(self, tmp_path):
        db_path = tmp_path / "dev.db"
        engine = create_engine(f"sqlite:///{db_path}")

        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE incidents (
                    id CHAR(32) NOT NULL PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    description TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    severity VARCHAR(20),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                """
                CREATE TABLE sessions (
                    id CHAR(32) NOT NULL PRIMARY KEY,
                    incident_id CHAR(32),
                    tier INTEGER NOT NULL,
                    model_provider VARCHAR(50),
                    model_id VARCHAR(200),
                    status VARCHAR(20) NOT NULL,
                    summary TEXT,
                    started_at DATETIME NOT NULL,
                    ended_at DATETIME
                )
                """
            )

            _patch_sqlite_dev_schema(conn, Base.metadata)

            columns = _column_names(conn, "sessions")

        assert "workflow_profile_id" in columns
        assert "model_config_id" in columns

    def test_adds_missing_mcp_status_columns(self, tmp_path):
        db_path = tmp_path / "dev.db"
        engine = create_engine(f"sqlite:///{db_path}")

        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE mcp_servers (
                    id CHAR(32) NOT NULL PRIMARY KEY,
                    org_id CHAR(32) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    transport VARCHAR(20) NOT NULL,
                    command VARCHAR(500),
                    args JSON,
                    url VARCHAR(1000),
                    token TEXT,
                    env_vars JSON,
                    is_active BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )

            _patch_sqlite_dev_schema(conn, Base.metadata)
            columns = _column_names(conn, "mcp_servers")

        assert "last_successful_call_at" in columns
        assert "last_error" in columns
