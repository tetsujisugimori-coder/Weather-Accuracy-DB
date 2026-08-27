"""SQLite connection, schema initialization, and status queries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from weatherdb.config import SCHEMA_PATH


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
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
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

