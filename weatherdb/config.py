"""Small, explicit configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_FORECAST_URL = (
    "https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json"
)
DEFAULT_HTTP_TIMEOUT = 20.0
DEFAULT_AREA_CODE = "140000"


def raw_forecast_path() -> Path:
    """Return the default directory for original JMA forecast responses."""
    return (Path.cwd() / "raw" / "jma" / "forecasts").resolve()


def database_path() -> Path:
    """Return the environment override or a DB below the current directory."""
    configured = os.environ.get("WEATHERDB_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / "data" / "weather.sqlite3").resolve()
