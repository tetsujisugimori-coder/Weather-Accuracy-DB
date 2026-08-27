"""Small, explicit configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "weather.sqlite3"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
KANAGAWA_MASTER_PATH = PROJECT_ROOT / "master" / "kanagawa.json"


def database_path() -> Path:
    """Return the configured database path without creating it."""
    configured = os.environ.get("WEATHERDB_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB_PATH

