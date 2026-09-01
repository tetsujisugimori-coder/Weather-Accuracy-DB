"""HTTP retrieval, raw storage, and transactional forecast persistence."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from weatherdb.db import require_phase2_schema
from weatherdb.forecast_parser import (
    ForecastDataError,
    ForecastDocument,
    decode_forecast_json,
    identify_documents,
    parse_document,
)

USER_AGENT = "Weather-Accuracy-DB/0.2 (+https://github.com/tetsujisugimori-coder/Weather-Accuracy-DB)"
MAX_ERROR_LENGTH = 500


class ForecastFetchError(RuntimeError):
    """A normal fetch/storage failure that the CLI can report without traceback."""


@dataclass(frozen=True)
class FetchSummary:
    raw_file_path: Path | None
    document_count: int
    completed_runs: int
    forecast_count: int
    skipped_documents: int


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_bytes(url: str, timeout: float) -> bytes:
    if timeout <= 0:
        raise ForecastFetchError("timeoutは0より大きい値を指定してください")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise ForecastFetchError(f"HTTP status {status} で取得に失敗しました")
            data = response.read()
    except HTTPError as exc:
        raise ForecastFetchError(f"HTTP status {exc.code} で取得に失敗しました") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ForecastFetchError(f"気象庁JSONへ接続できません: {reason}") from exc
    if not data:
        raise ForecastFetchError("気象庁JSONのHTTPレスポンスが空です")
    return data


def save_raw_bytes(raw: bytes, raw_dir: Path, fetched_at: datetime, area_code: str) -> Path:
    if not area_code.isascii() or not area_code.isdigit() or len(area_code) != 6:
        raise ForecastFetchError(f"地域コードが不正です: {area_code!r}")
    root = raw_dir.expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        suffix = hashlib.sha256(raw).hexdigest()[:12]
        nonce = uuid.uuid4().hex[:8]
        destination = root / f"{stamp}_{area_code}_forecast_{suffix}_{nonce}.json"
        temporary_name: str | None = None
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=root, prefix=".forecast-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(raw)
            temporary.flush()
            os.fsync(temporary.fileno())
        Path(temporary_name).replace(destination)
        return destination
    except OSError as exc:
        if "temporary_name" in locals() and temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise ForecastFetchError(f"raw JSONを保存できません: {exc}") from exc


def _message(exc: BaseException) -> str:
    value = str(exc).replace("\x00", "")
    return value[:MAX_ERROR_LENGTH]


class _CompletedElsewhere(RuntimeError):
    """Internal signal for a concurrent completed-document winner."""


def _completed_document_exists(
    connection: sqlite3.Connection, provider_id: int, document_sha256: str
) -> bool:
    return connection.execute(
        """
        SELECT 1 FROM forecast_runs
        WHERE provider_id = ? AND document_sha256 = ? AND status = 'completed'
        LIMIT 1
        """,
        (provider_id, document_sha256),
    ).fetchone() is not None


def _create_failed_run(
    connection: sqlite3.Connection,
    provider_id: int,
    fetched_at: str,
    source_url: str,
    error: BaseException,
    raw_file_path: Path | None = None,
    raw_sha256: str | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO forecast_runs
              (provider_id, fetched_at, source_url, raw_file_path, raw_file_sha256,
               status, error_message)
            VALUES (?, ?, ?, ?, ?, 'failed', ?)
            """,
            (provider_id, fetched_at, source_url,
             str(raw_file_path) if raw_file_path else None, raw_sha256, _message(error)),
        )


def _save_document(
    connection: sqlite3.Connection,
    provider_id: int,
    document: ForecastDocument,
    fetched_at: str,
    source_url: str,
    raw_file_path: Path,
    raw_sha256: str,
    known_areas: set[str],
    known_stations: set[str],
) -> tuple[bool, int]:
    if _completed_document_exists(connection, provider_id, document.sha256):
        return False, 0
    with connection:
        run_id = connection.execute(
            """
            INSERT INTO forecast_runs
              (provider_id, fetched_at, issued_at, document_type, source_url,
               raw_file_path, raw_file_sha256, document_sha256, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'started')
            """,
            (provider_id, fetched_at, document.issued_at, document.document_type,
             source_url, str(raw_file_path), raw_sha256, document.sha256),
        ).lastrowid
    try:
        records = parse_document(document, known_areas, known_stations)
        try:
            with connection:
                for record in records:
                    area_id = None
                    station_id = None
                    if record.target_kind == "area":
                        row = connection.execute(
                            "SELECT id FROM forecast_areas WHERE provider_id=? AND area_code=?",
                            (provider_id, record.target_code),
                        ).fetchone()
                        area_id = row[0] if row else None
                    else:
                        row = connection.execute(
                            "SELECT id FROM observation_stations WHERE provider_id=? AND station_code=?",
                            (provider_id, record.target_code),
                        ).fetchone()
                        station_id = row[0] if row else None
                    if area_id is None and station_id is None:
                        raise ForecastDataError(
                            f"対象コードをDBで解決できません: {record.target_code}"
                        )
                    connection.execute(
                        """
                        INSERT INTO forecasts
                          (forecast_run_id, forecast_area_id, station_id, target_start,
                           target_end, forecast_type, weather_code, weather_text,
                           precipitation_probability, high_temperature, low_temperature)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (run_id, area_id, station_id, record.target_start, record.target_end,
                         record.forecast_type, record.weather_code, record.weather_text,
                         record.precipitation_probability, record.high_temperature,
                         record.low_temperature),
                    )
                updated = connection.execute(
                    """
                    UPDATE forecast_runs
                    SET status='completed', error_message=NULL
                    WHERE id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM forecast_runs
                          WHERE provider_id = ?
                            AND document_sha256 = ?
                            AND status = 'completed'
                            AND id <> ?
                      )
                    """,
                    (run_id, provider_id, document.sha256, run_id),
                )
                if updated.rowcount != 1:
                    raise _CompletedElsewhere()
        except sqlite3.IntegrityError:
            if _completed_document_exists(connection, provider_id, document.sha256):
                with connection:
                    connection.execute("DELETE FROM forecast_runs WHERE id=?", (run_id,))
                return False, 0
            raise
        return True, len(records)
    except _CompletedElsewhere:
        with connection:
            connection.execute("DELETE FROM forecast_runs WHERE id=?", (run_id,))
        return False, 0
    except (ForecastDataError, sqlite3.Error, OSError) as exc:
        if connection.in_transaction:
            connection.rollback()
        with connection:
            connection.execute(
                "UPDATE forecast_runs SET status='failed', error_message=? WHERE id=?",
                (_message(exc), run_id),
            )
        raise


def _keep_or_discard_raw(
    connection: sqlite3.Connection,
    provider_id: int,
    fetched_at: str,
    source_url: str,
    raw_file_path: Path,
    raw_sha256: str,
) -> Path | None:
    """Delete an unreferenced all-duplicate raw, or retain and report cleanup failure."""
    referenced = connection.execute(
        "SELECT 1 FROM forecast_runs WHERE raw_file_path=? LIMIT 1",
        (str(raw_file_path),),
    ).fetchone()
    if referenced:
        return raw_file_path
    try:
        raw_file_path.unlink()
    except OSError as exc:
        cleanup_error = ForecastFetchError(
            f"重複取得の未参照raw JSONを削除できません: {exc}"
        )
        # If the file still exists, retain it as evidence referenced by a failed
        # run. If it disappeared concurrently, do not write a dead path.
        retained_path = raw_file_path if raw_file_path.exists() else None
        _create_failed_run(
            connection,
            provider_id,
            fetched_at,
            source_url,
            cleanup_error,
            retained_path,
            raw_sha256 if retained_path else None,
        )
        raise cleanup_error from exc
    return None


def fetch_and_store_forecast(
    connection: sqlite3.Connection,
    url: str,
    raw_dir: Path,
    timeout: float,
    area_code: str = "140000",
    http_get: Callable[[str, float], bytes] = fetch_bytes,
    now: Callable[[], datetime] = utc_now,
) -> FetchSummary:
    require_phase2_schema(connection)
    provider = connection.execute("SELECT id FROM providers WHERE code='JMA'").fetchone()
    if not provider:
        raise ForecastFetchError("JMA providerがありません。先に init を実行してください")
    provider_id = int(provider[0])
    area_count = connection.execute(
        "SELECT COUNT(*) FROM forecast_areas WHERE provider_id=?", (provider_id,)
    ).fetchone()[0]
    station_count = connection.execute(
        "SELECT COUNT(*) FROM observation_stations WHERE provider_id=?", (provider_id,)
    ).fetchone()[0]
    if area_count == 0 or station_count == 0:
        raise ForecastFetchError("地域・地点マスタがありません。先に import-areas を実行してください")

    fetched = now()
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        raise ForecastFetchError("取得日時にはUTC offsetが必要です")
    fetched_at = fetched.astimezone(timezone.utc).isoformat(timespec="seconds")
    raw_file: Path | None = None
    raw_sha256: str | None = None
    try:
        raw = http_get(url, timeout)
        if not raw:
            raise ForecastFetchError("気象庁JSONのHTTPレスポンスが空です")
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        raw_file = save_raw_bytes(raw, raw_dir, fetched, area_code)
        decoded = decode_forecast_json(raw)
        documents = identify_documents(decoded)
    except (ForecastFetchError, ForecastDataError, OSError) as exc:
        _create_failed_run(
            connection, provider_id, fetched_at, url, exc, raw_file, raw_sha256
        )
        raise

    known_areas = {
        row[0] for row in connection.execute(
            "SELECT area_code FROM forecast_areas WHERE provider_id=?", (provider_id,)
        )
    }
    known_stations = {
        row[0] for row in connection.execute(
            "SELECT station_code FROM observation_stations WHERE provider_id=?", (provider_id,)
        )
    }
    completed = forecasts = skipped = 0
    for document in documents:
        try:
            saved, record_count = _save_document(
                connection, provider_id, document, fetched_at, url, raw_file,
                raw_sha256, known_areas, known_stations
            )
        except (ForecastDataError, sqlite3.Error, OSError) as exc:
            raise ForecastFetchError(f"{document.document_type}予報を保存できません: {_message(exc)}") from exc
        if saved:
            completed += 1
            forecasts += record_count
        else:
            skipped += 1
    retained_raw = _keep_or_discard_raw(
        connection, provider_id, fetched_at, url, raw_file, raw_sha256
    )
    return FetchSummary(retained_raw, len(documents), completed, forecasts, skipped)
