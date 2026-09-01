"""SQLite connection, schema initialization, and status queries."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path
from typing import Any


class SchemaCompatibilityError(RuntimeError):
    """Raised when an existing database predates the Phase 2 schema."""


_PHASE2_FORECAST_COLUMNS = {"forecast_area_id", "station_id"}
_PHASE2_RUN_COLUMNS = {"document_type", "raw_file_sha256", "document_sha256"}
_RETRY_INDEX_NAME = "idx_forecast_runs_document"
_FORECAST_RUN_INDEX_NAME = "idx_forecasts_run"


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _require_phase2_columns(connection: sqlite3.Connection) -> None:
    """Reject an old Phase 1 DB without changing or deleting it."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "forecasts" not in tables or "forecast_runs" not in tables:
        raise SchemaCompatibilityError("DBが未初期化です。先に init を実行してください")
    if not _PHASE2_FORECAST_COLUMNS <= _table_columns(connection, "forecasts") or not (
        _PHASE2_RUN_COLUMNS <= _table_columns(connection, "forecast_runs")
    ):
        raise SchemaCompatibilityError(
            "旧Phase 1スキーマです。DBを削除せずバックアップへ移動し、"
            "init と import-areas でPhase 2 DBを新規作成してください"
        )


def require_phase2_schema(connection: sqlite3.Connection) -> None:
    """Require the current Phase 2 columns and retry/index definitions."""
    _require_phase2_columns(connection)
    retry_index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (_RETRY_INDEX_NAME,),
    ).fetchone()
    forecast_run_index = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (_FORECAST_RUN_INDEX_NAME,),
    ).fetchone()
    normalized_sql = " ".join((retry_index[0] if retry_index else "").lower().split())
    if "status = 'completed'" not in normalized_sql or not forecast_run_index:
        raise SchemaCompatibilityError(
            "Phase 2スキーマ更新が必要です。DBをバックアップしたうえで "
            "init を再実行してください"
        )


def require_phase2_database(path: Path) -> None:
    """Inspect an existing DB read-only before opening the normal WAL connection."""
    if not path.exists() or path.stat().st_size == 0:
        return
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        # initialize() may safely replace only Phase 2 indexes after this check;
        # Phase 1 tables are still rejected before a normal writable connection.
        _require_phase2_columns(connection)
    finally:
        connection.close()


def connect(path: Path) -> sqlite3.Connection:
    """Open SQLite with the safety settings required by this project."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(path: Path) -> None:
    """Create the database idempotently and register the JMA provider."""
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = files("weatherdb.resources").joinpath("schema.sql").read_text(encoding="utf-8")
    require_phase2_database(path)
    connection = connect(path)
    try:
        connection.executescript(schema)
        with connection:
            connection.execute(
                """
                INSERT INTO providers (code, name)
                VALUES ('JMA', '気象庁')
                ON CONFLICT(code) DO UPDATE SET name = excluded.name
                """
            )
    finally:
        connection.close()


def database_status(path: Path) -> dict[str, Any]:
    """Return counts and latest timestamps used by the status command."""
    connection = connect(path)
    try:
        counts = {}
        for label, table in (
            ("providers", "providers"),
            ("forecast_areas", "forecast_areas"),
            ("observation_stations", "observation_stations"),
            ("forecast_runs", "forecast_runs"),
            ("forecasts", "forecasts"),
            ("observations", "observations"),
        ):
            counts[label] = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        latest = connection.execute(
            """
            SELECT
              (SELECT MAX(issued_at) FROM forecast_runs) AS issued_at,
              (SELECT MAX(fetched_at) FROM forecast_runs) AS fetched_at,
              (SELECT MAX(observed_at) FROM observations) AS observed_at
            """
        ).fetchone()
        last_run = connection.execute(
            """
            SELECT status, error_message
            FROM forecast_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return {
            "path": str(path.resolve()),
            **counts,
            "latest_issued_at": latest["issued_at"],
            "latest_fetched_at": latest["fetched_at"],
            "latest_observed_at": latest["observed_at"],
            "last_run_status": last_run["status"] if last_run else None,
            "last_run_error": last_run["error_message"] if last_run else None,
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
        }
    finally:
        connection.close()
