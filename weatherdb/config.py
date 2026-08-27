"""Small, explicit configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path


def database_path() -> Path:
    """Return the environment override or a DB below the current directory."""
    configured = os.environ.get("WEATHERDB_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / "data" / "weather.sqlite3").resolve()
