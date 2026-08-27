"""Import the versioned Kanagawa area and observation-station master."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from weatherdb.config import KANAGAWA_MASTER_PATH


AREA_LEVELS = {"prefecture", "primary", "grouped_municipality", "municipality"}


class MasterDataError(ValueError):
    """Raised when bundled master data is structurally invalid."""


def load_master(path: Path = KANAGAWA_MASTER_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MasterDataError(f"地域マスタを読み込めません: {exc}") from exc
    validate_master(data)
    return data


def validate_master(data: dict[str, Any]) -> None:
    if not isinstance(data.get("areas"), list) or not isinstance(data.get("stations"), list):
        raise MasterDataError("areas と stations は配列である必要があります")

    area_codes: set[str] = set()
    for area in data["areas"]:
        required = {"area_code", "name", "area_level"}
        if not required <= area.keys() or area["area_level"] not in AREA_LEVELS:
            raise MasterDataError(f"不正な地域レコードです: {area!r}")
        if area["area_code"] in area_codes:
            raise MasterDataError(f"地域コードが重複しています: {area['area_code']}")
        parent = area.get("parent_area_code")
        if parent is not None and parent not in area_codes:
            raise MasterDataError(f"親地域が先に定義されていません: {parent}")
        area_codes.add(area["area_code"])

    station_codes: set[str] = set()
    for station in data["stations"]:
        required = {"station_code", "name", "latitude", "longitude", "area_codes"}
        if not required <= station.keys():
            raise MasterDataError(f"不正な観測地点レコードです: {station!r}")
        if station["station_code"] in station_codes:
            raise MasterDataError(f"観測地点コードが重複しています: {station['station_code']}")
        unknown = set(station["area_codes"]) - area_codes
        if unknown:
            raise MasterDataError(f"観測地点の対応地域が不明です: {sorted(unknown)}")
        station_codes.add(station["station_code"])


def import_kanagawa_master(connection: sqlite3.Connection, path: Path = KANAGAWA_MASTER_PATH) -> tuple[int, int]:
    """Upsert the bundled master atomically and return area/station totals."""
    data = load_master(path)
    provider = connection.execute("SELECT id FROM providers WHERE code = 'JMA'").fetchone()
    if provider is None:
        raise MasterDataError("JMA provider がありません。先に init を実行してください")
    provider_id = provider[0]

    with connection:
        for area in data["areas"]:
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
                    data["verified_at"],
                ),
            )

        for station in data["stations"]:
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
                    station["station_code"],
                    station["name"],
                    station.get("name_en"),
                    station.get("station_type"),
                    station.get("element_flags"),
                    station["latitude"],
                    station["longitude"],
                    station.get("elevation"),
                    station.get("active_from"),
                    station.get("active_to"),
                    data["sources"]["stations"],
                    data["verified_at"],
                ),
            )
            station_id = connection.execute(
                "SELECT id FROM observation_stations WHERE provider_id = ? AND station_code = ?",
                (provider_id, station["station_code"]),
            ).fetchone()[0]
            for area_code in station["area_codes"]:
                area_id = connection.execute(
                    "SELECT id FROM forecast_areas WHERE provider_id = ? AND area_code = ?",
                    (provider_id, area_code),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO station_area_memberships
                      (station_id, forecast_area_id, relation_type, valid_from, valid_to)
                    VALUES (?, ?, 'located_in', NULL, NULL)
                    ON CONFLICT(station_id, forecast_area_id, relation_type) DO UPDATE SET
                      valid_to = excluded.valid_to
                    """,
                    (station_id, area_id),
                )

        connection.execute(
            """
            INSERT INTO master_imports (master_name, source_url, verified_at)
            VALUES (?, ?, ?)
            ON CONFLICT(master_name, verified_at) DO NOTHING
            """,
            ("kanagawa-phase1", data["sources"]["areas"], data["verified_at"]),
        )

    area_count = connection.execute("SELECT COUNT(*) FROM forecast_areas").fetchone()[0]
    station_count = connection.execute("SELECT COUNT(*) FROM observation_stations").fetchone()[0]
    return area_count, station_count

