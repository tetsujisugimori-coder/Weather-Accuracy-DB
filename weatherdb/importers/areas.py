"""Import the versioned Kanagawa area and observation-station master."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any


AREA_LEVELS = {"prefecture", "primary", "grouped_municipality", "municipality"}
MASTER_NAME = "kanagawa-phase1"


class MasterDataError(ValueError):
    """Raised when master data is missing, unreadable, or structurally invalid."""


def load_master(path: Path | None = None) -> dict[str, Any]:
    """Load a custom master or the read-only master bundled in the package."""
    try:
        if path is None:
            text = files("weatherdb.resources").joinpath("kanagawa.json").read_text(
                encoding="utf-8"
            )
        else:
            text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MasterDataError(f"地域マスタを読み込めません: {exc}") from exc
    validate_master(data)
    return data


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MasterDataError(f"{label} は空でない文字列である必要があります")
    return value


def _optional_string(record: dict[str, Any], key: str, label: str) -> None:
    value = record.get(key)
    if value is not None:
        _non_empty_string(value, label)


def _iso_date(value: Any, label: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    text = _non_empty_string(value, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise MasterDataError(f"{label} はYYYY-MM-DD形式の日付である必要があります") from exc
    if parsed.isoformat() != text:
        raise MasterDataError(f"{label} はYYYY-MM-DD形式の日付である必要があります")
    return text


def _optional_date_range(record: dict[str, Any], prefix: str, label: str) -> None:
    start = _iso_date(record.get(f"{prefix}_from"), f"{label}.{prefix}_from")
    end = _iso_date(record.get(f"{prefix}_to"), f"{label}.{prefix}_to")
    if start is not None and end is not None and end <= start:
        raise MasterDataError(f"{label} の終了日は開始日より後である必要があります")


def _reject_future_date(
    record: dict[str, Any],
    key: str,
    *,
    subject: str,
    verified_at: str,
) -> None:
    value = record.get(key)
    if value is not None and value > verified_at:
        raise MasterDataError(
            f"{subject} の {key} は verified_at 以前である必要があります: "
            f"{key}={value} / verified_at={verified_at}"
        )


def _coordinate(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MasterDataError(f"{label} は数値である必要があります")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise MasterDataError(f"{label} は有限の数値である必要があります") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise MasterDataError(f"{label} は{minimum:g}以上{maximum:g}以下である必要があります")
    return number


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MasterDataError(f"{label} は有限の数値である必要があります")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise MasterDataError(f"{label} は有限の数値である必要があります") from exc
    if not math.isfinite(number):
        raise MasterDataError(f"{label} は有限の数値である必要があります")
    return number


def _validate_master(data: Any) -> None:
    if not isinstance(data, dict):
        raise MasterDataError("JSONルートは辞書である必要があります")

    verified_at = _iso_date(data.get("verified_at"), "verified_at", required=True)
    assert verified_at is not None

    sources = data.get("sources")
    if not isinstance(sources, dict):
        raise MasterDataError("sources は辞書である必要があります")
    _non_empty_string(sources.get("areas"), "sources.areas")
    _non_empty_string(sources.get("stations"), "sources.stations")

    areas = data.get("areas")
    stations = data.get("stations")
    if not isinstance(areas, list) or not isinstance(stations, list):
        raise MasterDataError("areas と stations は配列である必要があります")

    area_codes: set[str] = set()
    parents: dict[str, str | None] = {}
    for index, area in enumerate(areas):
        label = f"areas[{index}]"
        if not isinstance(area, dict):
            raise MasterDataError(f"{label} は辞書である必要があります")
        area_code = _non_empty_string(area.get("area_code"), f"{label}.area_code")
        _non_empty_string(area.get("name"), f"{label}.name")
        area_level = _non_empty_string(area.get("area_level"), f"{label}.area_level")
        if area_level not in AREA_LEVELS:
            raise MasterDataError(f"{label}.area_level が不正です: {area_level}")
        _optional_string(area, "name_en", f"{label}.name_en")
        parent = area.get("parent_area_code")
        if parent is not None:
            parent = _non_empty_string(parent, f"{label}.parent_area_code")
        _optional_date_range(area, "valid", label)
        for key in ("valid_from", "valid_to"):
            _reject_future_date(
                area,
                key,
                subject=f"地域 {area_code}",
                verified_at=verified_at,
            )
        if area_code in area_codes:
            raise MasterDataError(f"地域コードが重複しています: {area_code}")
        area_codes.add(area_code)
        parents[area_code] = parent

    for area_code, parent in parents.items():
        if parent is not None and parent not in area_codes:
            raise MasterDataError(f"地域 {area_code} の親地域が不明です: {parent}")
        visited = {area_code}
        current = parent
        while current is not None:
            if current in visited:
                raise MasterDataError(f"地域階層が循環しています: {area_code}")
            visited.add(current)
            current = parents[current]

    station_codes: set[str] = set()
    for index, station in enumerate(stations):
        label = f"stations[{index}]"
        if not isinstance(station, dict):
            raise MasterDataError(f"{label} は辞書である必要があります")
        station_code = _non_empty_string(
            station.get("station_code"), f"{label}.station_code"
        )
        _non_empty_string(station.get("name"), f"{label}.name")
        _optional_string(station, "name_en", f"{label}.name_en")
        _optional_string(station, "station_type", f"{label}.station_type")
        _optional_string(station, "element_flags", f"{label}.element_flags")
        _coordinate(station.get("latitude"), f"{label}.latitude", -90, 90)
        _coordinate(station.get("longitude"), f"{label}.longitude", -180, 180)
        elevation = station.get("elevation")
        if elevation is not None:
            _finite_number(elevation, f"{label}.elevation")
        _optional_date_range(station, "active", label)
        for key in ("active_from", "active_to"):
            _reject_future_date(
                station,
                key,
                subject=f"観測地点 {station_code}",
                verified_at=verified_at,
            )

        station_areas = station.get("area_codes")
        if not isinstance(station_areas, list):
            raise MasterDataError(f"{label}.area_codes は文字列の配列である必要があります")
        validated_areas = [
            _non_empty_string(code, f"{label}.area_codes") for code in station_areas
        ]
        if len(set(validated_areas)) != len(validated_areas):
            raise MasterDataError(f"{label}.area_codes に重複があります")
        unknown = set(validated_areas) - area_codes
        if unknown:
            raise MasterDataError(f"観測地点の対応地域が不明です: {sorted(unknown)}")
        if station_code in station_codes:
            raise MasterDataError(f"観測地点コードが重複しています: {station_code}")
        station_codes.add(station_code)


def validate_master(data: Any) -> None:
    """Validate all data used by the importer and expose one public error type."""
    try:
        _validate_master(data)
    except MasterDataError:
        raise
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise MasterDataError(f"地域マスタの構造が不正です: {exc}") from exc


def _master_content_sha256(data: dict[str, Any]) -> str:
    """Hash only validated importer inputs in a deterministic representation."""
    normalized_areas = []
    for area in sorted(data["areas"], key=lambda item: item["area_code"]):
        normalized_areas.append(
            {
                "area_code": area["area_code"],
                "name": area["name"],
                "name_en": area.get("name_en"),
                "parent_area_code": area.get("parent_area_code"),
                "area_level": area["area_level"],
                "valid_from": area.get("valid_from"),
                "valid_to": area.get("valid_to"),
            }
        )

    normalized_stations = []
    for station in sorted(data["stations"], key=lambda item: item["station_code"]):
        elevation = station.get("elevation")
        normalized_stations.append(
            {
                "station_code": station["station_code"],
                "name": station["name"],
                "name_en": station.get("name_en"),
                "station_type": station.get("station_type"),
                "element_flags": station.get("element_flags"),
                "latitude": _coordinate(
                    station["latitude"], "station.latitude", -90, 90
                ),
                "longitude": _coordinate(
                    station["longitude"], "station.longitude", -180, 180
                ),
                "elevation": (
                    _finite_number(elevation, "station.elevation")
                    if elevation is not None
                    else None
                ),
                "active_from": station.get("active_from"),
                "active_to": station.get("active_to"),
                "area_codes": sorted(station["area_codes"]),
            }
        )

    normalized = {
        "verified_at": data["verified_at"],
        "sources": {
            "areas": data["sources"]["areas"],
            "stations": data["sources"]["stations"],
        },
        "areas": normalized_areas,
        "stations": normalized_stations,
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ordered_areas(areas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return parents before children without requiring source-file ordering."""
    remaining = {area["area_code"]: area for area in areas}
    ordered: list[dict[str, Any]] = []
    inserted: set[str] = set()
    while remaining:
        ready = [
            code
            for code, area in remaining.items()
            if area.get("parent_area_code") is None
            or area["parent_area_code"] in inserted
        ]
        if not ready:
            raise MasterDataError("地域階層を親から順に並べられません")
        for code in ready:
            ordered.append(remaining.pop(code))
            inserted.add(code)
    return ordered


def import_kanagawa_master(
    connection: sqlite3.Connection, path: Path | None = None
) -> tuple[int, int]:
    """Synchronize the versioned master atomically and return JMA totals."""
    data = load_master(path)
    verified_at = data["verified_at"]
    content_sha256 = _master_content_sha256(data)

    if connection.in_transaction:
        raise MasterDataError("未完了のトランザクションがあるためマスタを取り込めません")

    try:
        connection.execute("BEGIN IMMEDIATE")
        provider = connection.execute(
            "SELECT id FROM providers WHERE code = 'JMA'"
        ).fetchone()
        if provider is None:
            raise MasterDataError("JMA provider がありません。先に init を実行してください")
        provider_id = provider[0]

        latest = connection.execute(
            """
            SELECT verified_at, content_sha256
            FROM master_imports
            WHERE master_name = ?
            ORDER BY verified_at DESC
            LIMIT 1
            """,
            (MASTER_NAME,),
        ).fetchone()
        if latest is not None and verified_at < latest["verified_at"]:
            raise MasterDataError(
                f"古いマスタは取り込めません: 今回 {verified_at} / "
                f"取込済み最新 {latest['verified_at']}"
            )
        if latest is not None and verified_at == latest["verified_at"]:
            if content_sha256 != latest["content_sha256"]:
                raise MasterDataError(
                    f"同じ確認日のマスタ内容が一致しません: {verified_at}"
                )
            area_count = connection.execute(
                "SELECT COUNT(*) FROM forecast_areas WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()[0]
            station_count = connection.execute(
                "SELECT COUNT(*) FROM observation_stations WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()[0]
            connection.commit()
            return area_count, station_count

        revival_starts: dict[str, str] = {}
        for station in data["stations"]:
            if station.get("active_to") is not None:
                continue
            station_code = station["station_code"]
            existing = connection.execute(
                """
                SELECT active_to
                FROM observation_stations
                WHERE provider_id = ? AND station_code = ?
                """,
                (provider_id, station_code),
            ).fetchone()
            if existing is None or existing["active_to"] is None:
                continue
            revival_start = station.get("active_from") or verified_at
            if revival_start < existing["active_to"]:
                raise MasterDataError(
                    f"観測地点 {station_code} の復活開始日が直前の終了日より前です: "
                    f"開始日候補 {revival_start} / 直前の終了日 {existing['active_to']}"
                )
            revival_starts[station_code] = revival_start

        current_area_codes = {area["area_code"] for area in data["areas"]}
        for area in _ordered_areas(data["areas"]):
            parent_id = None
            if area.get("parent_area_code"):
                parent_id = connection.execute(
                    "SELECT id FROM forecast_areas WHERE provider_id = ? AND area_code = ?",
                    (provider_id, area["parent_area_code"]),
                ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO forecast_areas
                  (provider_id, area_code, name, name_en, parent_area_id, area_level,
                   valid_from, valid_to, source_url, master_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id, area_code) DO UPDATE SET
                  name = excluded.name,
                  name_en = excluded.name_en,
                  parent_area_id = excluded.parent_area_id,
                  area_level = excluded.area_level,
                  valid_from = excluded.valid_from,
                  valid_to = excluded.valid_to,
                  source_url = excluded.source_url,
                  master_verified_at = excluded.master_verified_at
                """,
                (
                    provider_id,
                    area["area_code"],
                    area["name"],
                    area.get("name_en"),
                    parent_id,
                    area["area_level"],
                    area.get("valid_from"),
                    area.get("valid_to"),
                    data["sources"]["areas"],
                    verified_at,
                ),
            )

        active_areas = connection.execute(
            """
            SELECT id, area_code, valid_from
            FROM forecast_areas
            WHERE provider_id = ? AND valid_to IS NULL
            """,
            (provider_id,),
        ).fetchall()
        for area in active_areas:
            if area["area_code"] in current_area_codes:
                continue
            if area["valid_from"] is not None and verified_at <= area["valid_from"]:
                raise MasterDataError(
                    "地域の終了日は開始日より後である必要があります: "
                    f"{area['area_code']}"
                )
            connection.execute(
                """
                UPDATE forecast_areas
                SET valid_to = ?, master_verified_at = ?
                WHERE id = ? AND valid_to IS NULL
                """,
                (verified_at, verified_at, area["id"]),
            )

        desired_memberships: set[tuple[str, str]] = set()
        current_station_codes: set[str] = set()
        retired_station_ends: dict[str, str] = {}
        membership_starts: dict[str, str] = {}
        for station in data["stations"]:
            station_code = station["station_code"]
            current_station_codes.add(station_code)
            active_to = station.get("active_to")
            is_retired = active_to is not None and active_to <= verified_at
            existing = connection.execute(
                """
                SELECT active_from, active_to
                FROM observation_stations
                WHERE provider_id = ? AND station_code = ?
                """,
                (provider_id, station_code),
            ).fetchone()

            if existing is None:
                active_from = (
                    station.get("active_from")
                    if is_retired
                    else station.get("active_from") or verified_at
                )
            elif is_retired:
                active_from = station.get("active_from") or existing["active_from"]
            elif station_code in revival_starts:
                active_from = revival_starts[station_code]
            else:
                active_from = (
                    existing["active_from"]
                    or station.get("active_from")
                    or verified_at
                )

            if active_to is not None and active_from is not None and active_to <= active_from:
                raise MasterDataError(
                    "観測地点の終了日は開始日より後である必要があります: "
                    f"{station_code}"
                )

            connection.execute(
                """
                INSERT INTO observation_stations
                  (provider_id, station_code, name, name_en, station_type, element_flags,
                   latitude, longitude, elevation, active_from, active_to,
                   source_url, master_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id, station_code) DO UPDATE SET
                  name = excluded.name,
                  name_en = excluded.name_en,
                  station_type = excluded.station_type,
                  element_flags = excluded.element_flags,
                  latitude = excluded.latitude,
                  longitude = excluded.longitude,
                  elevation = excluded.elevation,
                  active_from = excluded.active_from,
                  active_to = excluded.active_to,
                  source_url = excluded.source_url,
                  master_verified_at = excluded.master_verified_at
                """,
                (
                    provider_id,
                    station_code,
                    station["name"],
                    station.get("name_en"),
                    station.get("station_type"),
                    station.get("element_flags"),
                    _coordinate(station["latitude"], "station.latitude", -90, 90),
                    _coordinate(
                        station["longitude"], "station.longitude", -180, 180
                    ),
                    (
                        _finite_number(station["elevation"], "station.elevation")
                        if station.get("elevation") is not None
                        else None
                    ),
                    active_from,
                    active_to,
                    data["sources"]["stations"],
                    verified_at,
                ),
            )
            if is_retired:
                retired_station_ends[station_code] = active_to
            else:
                desired_memberships.update(
                    (station_code, area_code) for area_code in station["area_codes"]
                )
                membership_starts[station_code] = revival_starts.get(
                    station_code, verified_at
                )

        active_stations = connection.execute(
            """
            SELECT id, station_code, active_from
            FROM observation_stations
            WHERE provider_id = ? AND active_to IS NULL
            """,
            (provider_id,),
        ).fetchall()
        for station in active_stations:
            if station["station_code"] in current_station_codes:
                continue
            if (
                station["active_from"] is not None
                and verified_at <= station["active_from"]
            ):
                raise MasterDataError(
                    "観測地点の終了日は開始日より後である必要があります: "
                    f"{station['station_code']}"
                )
            connection.execute(
                """
                UPDATE observation_stations
                SET active_to = ?, master_verified_at = ?
                WHERE id = ? AND active_to IS NULL
                """,
                (verified_at, verified_at, station["id"]),
            )

        active_rows = connection.execute(
            """
            SELECT membership.id, membership.valid_from,
                   station.station_code, area.area_code
            FROM station_area_memberships AS membership
            JOIN observation_stations AS station ON station.id = membership.station_id
            JOIN forecast_areas AS area ON area.id = membership.forecast_area_id
            WHERE station.provider_id = ?
              AND membership.relation_type = 'located_in'
              AND membership.valid_to IS NULL
            """,
            (provider_id,),
        ).fetchall()
        active_keys = {
            (row["station_code"], row["area_code"]): row for row in active_rows
        }

        for key, row in active_keys.items():
            if key in desired_memberships:
                continue
            close_at = retired_station_ends.get(key[0], verified_at)
            if close_at <= row["valid_from"]:
                raise MasterDataError(
                    "関係の終了日は開始日より後である必要があります: "
                    f"{key[0]} -> {key[1]}"
                )
            connection.execute(
                "UPDATE station_area_memberships SET valid_to = ? WHERE id = ?",
                (close_at, row["id"]),
            )

        for station_code, area_code in sorted(desired_memberships):
            if (station_code, area_code) in active_keys:
                continue
            station_id = connection.execute(
                "SELECT id FROM observation_stations WHERE provider_id = ? AND station_code = ?",
                (provider_id, station_code),
            ).fetchone()[0]
            area_id = connection.execute(
                "SELECT id FROM forecast_areas WHERE provider_id = ? AND area_code = ?",
                (provider_id, area_code),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO station_area_memberships
                  (station_id, forecast_area_id, relation_type, valid_from, valid_to)
                VALUES (?, ?, 'located_in', ?, NULL)
                """,
                (station_id, area_id, membership_starts[station_code]),
            )

        connection.execute(
            """
            INSERT INTO master_imports
              (master_name, source_url, verified_at, content_sha256)
            VALUES (?, ?, ?, ?)
            """,
            (MASTER_NAME, data["sources"]["areas"], verified_at, content_sha256),
        )
        area_count = connection.execute(
            "SELECT COUNT(*) FROM forecast_areas WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()[0]
        station_count = connection.execute(
            "SELECT COUNT(*) FROM observation_stations WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()[0]
        connection.commit()
        return area_count, station_count
    except Exception:
        connection.rollback()
        raise
