from __future__ import annotations

import contextlib
import copy
import io
import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from typing import Any

from weatherdb.cli import main
from weatherdb.db import connect, initialize
from weatherdb.importers.areas import (
    MasterDataError,
    import_kanagawa_master,
    load_master,
    validate_master,
)


class MasterUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        test_data = Path(__file__).resolve().parent / "testdata"
        unique = uuid.uuid4().hex
        self.db_path = test_data / f"master-{unique}.sqlite3"
        self.master_paths: list[Path] = []
        initialize(self.db_path)
        self.connection = connect(self.db_path)
        import_kanagawa_master(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        for path in self.master_paths:
            if path.exists():
                path.unlink()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def write_master(self, data: Any) -> Path:
        path = self.db_path.with_name(f"{self.db_path.stem}-{len(self.master_paths)}.json")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.master_paths.append(path)
        return path

    @staticmethod
    def station(data: dict[str, Any], station_code: str) -> dict[str, Any]:
        return next(
            station
            for station in data["stations"]
            if station["station_code"] == station_code
        )

    def membership_rows(self, station_code: str) -> list[tuple[str, str, str | None]]:
        return [
            tuple(row)
            for row in self.connection.execute(
                """
                SELECT area.area_code, membership.valid_from, membership.valid_to
                FROM station_area_memberships AS membership
                JOIN observation_stations AS station ON station.id = membership.station_id
                JOIN forecast_areas AS area ON area.id = membership.forecast_area_id
                WHERE station.station_code = ?
                  AND membership.relation_type = 'located_in'
                ORDER BY membership.valid_from, area.area_code
                """,
                (station_code,),
            )
        ]

    def test_changed_membership_closes_old_period_and_is_idempotent(self) -> None:
        changed = copy.deepcopy(load_master())
        changed["verified_at"] = "2026-08-28"
        self.station(changed, "46106")["area_codes"] = ["140012"]
        path = self.write_master(changed)

        import_kanagawa_master(self.connection, path)
        self.assertEqual(
            self.membership_rows("46106"),
            [
                ("140011", "2026-08-27", "2026-08-28"),
                ("140012", "2026-08-28", None),
            ],
        )

        import_kanagawa_master(self.connection, path)
        self.assertEqual(len(self.membership_rows("46106")), 2)
        active_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM station_area_memberships AS membership
            JOIN observation_stations AS station ON station.id = membership.station_id
            WHERE station.station_code = '46106'
              AND membership.relation_type = 'located_in'
              AND membership.valid_to IS NULL
            """
        ).fetchone()[0]
        self.assertEqual(active_count, 1)

    def test_ended_membership_can_return_as_a_new_period(self) -> None:
        changed = copy.deepcopy(load_master())
        changed["verified_at"] = "2026-08-28"
        self.station(changed, "46106")["area_codes"] = ["140012"]
        import_kanagawa_master(self.connection, self.write_master(changed))

        restored = copy.deepcopy(load_master())
        restored["verified_at"] = "2026-08-29"
        import_kanagawa_master(self.connection, self.write_master(restored))

        self.assertEqual(
            self.membership_rows("46106"),
            [
                ("140011", "2026-08-27", "2026-08-28"),
                ("140012", "2026-08-28", "2026-08-29"),
                ("140011", "2026-08-29", None),
            ],
        )

    def test_missing_station_is_ended_without_touching_verification_target(self) -> None:
        station_id = self.connection.execute(
            "SELECT id FROM observation_stations WHERE station_code = '46106'"
        ).fetchone()[0]
        area_id = self.connection.execute(
            "SELECT id FROM forecast_areas WHERE area_code = '140024'"
        ).fetchone()[0]
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO station_area_memberships
                  (station_id, forecast_area_id, relation_type, valid_from, valid_to)
                VALUES (?, ?, 'verification_target', '2026-08-27', NULL)
                """,
                (station_id, area_id),
            )

        changed = copy.deepcopy(load_master())
        changed["verified_at"] = "2026-08-28"
        changed["stations"] = [
            station
            for station in changed["stations"]
            if station["station_code"] != "46106"
        ]
        import_kanagawa_master(self.connection, self.write_master(changed))

        active_to = self.connection.execute(
            "SELECT active_to FROM observation_stations WHERE station_code = '46106'"
        ).fetchone()[0]
        self.assertEqual(active_to, "2026-08-28")
        relations = dict(
            self.connection.execute(
                """
                SELECT relation_type, valid_to
                FROM station_area_memberships
                WHERE station_id = ?
                """,
                (station_id,),
            ).fetchall()
        )
        self.assertEqual(relations["located_in"], "2026-08-28")
        self.assertIsNone(relations["verification_target"])


class MasterValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        test_data = Path(__file__).resolve().parent / "testdata"
        unique = uuid.uuid4().hex
        self.db_path = test_data / f"invalid-{unique}.sqlite3"
        self.master_path = test_data / f"invalid-{unique}.json"

    def tearDown(self) -> None:
        if self.master_path.exists():
            self.master_path.unlink()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def test_structural_errors_are_always_master_data_errors(self) -> None:
        base = load_master()
        invalid_cases: list[tuple[str, Any]] = []
        invalid_cases.append(("root-list", []))

        missing_sources = copy.deepcopy(base)
        missing_sources.pop("sources")
        invalid_cases.append(("missing-sources", missing_sources))

        invalid_date = copy.deepcopy(base)
        invalid_date["verified_at"] = "not-a-date"
        invalid_cases.append(("invalid-verified-at", invalid_date))

        numeric_area = copy.deepcopy(base)
        numeric_area["areas"][0] = 42
        invalid_cases.append(("numeric-area", numeric_area))

        invalid_latitude = copy.deepcopy(base)
        invalid_latitude["stations"][0]["latitude"] = True
        invalid_cases.append(("boolean-latitude", invalid_latitude))

        invalid_longitude = copy.deepcopy(base)
        invalid_longitude["stations"][0]["longitude"] = 181
        invalid_cases.append(("out-of-range-longitude", invalid_longitude))

        invalid_area_codes = copy.deepcopy(base)
        invalid_area_codes["stations"][0]["area_codes"] = "140021"
        invalid_cases.append(("non-list-area-codes", invalid_area_codes))

        unknown_area = copy.deepcopy(base)
        unknown_area["stations"][0]["area_codes"] = ["999999"]
        invalid_cases.append(("unknown-area", unknown_area))

        for label, data in invalid_cases:
            with self.subTest(label=label):
                with self.assertRaises(MasterDataError):
                    validate_master(data)

    def test_load_master_converts_invalid_root_to_master_data_error(self) -> None:
        self.master_path.write_text("[]", encoding="utf-8")
        with self.assertRaises(MasterDataError):
            load_master(self.master_path)

    def test_cli_invalid_master_returns_one_without_traceback(self) -> None:
        initialize(self.db_path)
        self.master_path.write_text("[]", encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "--db",
                    str(self.db_path),
                    "import-areas",
                    "--master",
                    str(self.master_path),
                ]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("エラー:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class ForecastPeriodConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        test_data = Path(__file__).resolve().parent / "testdata"
        self.db_path = test_data / f"period-{uuid.uuid4().hex}.sqlite3"
        initialize(self.db_path)
        self.connection = connect(self.db_path)
        import_kanagawa_master(self.connection)
        provider_id = self.connection.execute(
            "SELECT id FROM providers WHERE code = 'JMA'"
        ).fetchone()[0]
        self.area_id = self.connection.execute(
            "SELECT id FROM forecast_areas WHERE area_code = '140010'"
        ).fetchone()[0]
        with self.connection:
            self.run_id = self.connection.execute(
                """
                INSERT INTO forecast_runs
                  (provider_id, fetched_at, issued_at, source_url, content_sha256, status)
                VALUES (?, '2026-08-28T00:00:00Z', '2026-08-27T23:00:00Z',
                        'https://example.invalid', 'period-hash', 'completed')
                """,
                (provider_id,),
            ).lastrowid

    def tearDown(self) -> None:
        self.connection.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def insert_period(self, start: str, end: str, forecast_type: str) -> None:
        self.connection.execute(
            """
            INSERT INTO forecasts
              (forecast_run_id, forecast_area_id, target_start, target_end, forecast_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.run_id, self.area_id, start, end, forecast_type),
        )

    def test_only_strictly_increasing_forecast_period_is_accepted(self) -> None:
        self.insert_period(
            "2026-08-28T00:00:00Z", "2026-08-29T00:00:00Z", "valid"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_period(
                "2026-08-29T00:00:00Z", "2026-08-29T00:00:00Z", "equal"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_period(
                "2026-08-30T00:00:00Z", "2026-08-29T00:00:00Z", "reversed"
            )


if __name__ == "__main__":
    unittest.main()

