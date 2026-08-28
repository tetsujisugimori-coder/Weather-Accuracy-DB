from __future__ import annotations

import contextlib
import copy
import io
import json
import sqlite3
import tempfile
import unittest
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
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="weatherdb-master-"
        )
        self.db_path = Path(self.temporary_directory.name) / "master.sqlite3"
        self.master_paths: list[Path] = []
        initialize(self.db_path)
        self.connection = connect(self.db_path)
        import_kanagawa_master(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def write_master(self, data: Any, *, indent: int | None = None) -> Path:
        path = self.db_path.with_name(f"{self.db_path.stem}-{len(self.master_paths)}.json")
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8"
        )
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

    def database_snapshot(self) -> dict[str, list[tuple[Any, ...]]]:
        tables = (
            "forecast_areas",
            "observation_stations",
            "station_area_memberships",
            "master_imports",
        )
        return {
            table: [
                tuple(row)
                for row in self.connection.execute(
                    f"SELECT * FROM {table} ORDER BY id"
                ).fetchall()
            ]
            for table in tables
        }

    def test_older_master_is_rejected_without_any_master_state_change(self) -> None:
        newer = copy.deepcopy(load_master())
        newer["verified_at"] = "2026-08-29"
        newer["areas"][0]["name"] = "新しい地域名"
        self.station(newer, "46106")["name"] = "新しい地点名"
        self.station(newer, "46106")["latitude"] = 35.45
        import_kanagawa_master(self.connection, self.write_master(newer))
        before = self.database_snapshot()

        older = copy.deepcopy(load_master())
        older["verified_at"] = "2026-08-28"
        older["areas"][0]["name"] = "古い地域名"
        self.station(older, "46106")["name"] = "古い地点名"
        self.station(older, "46106")["area_codes"] = ["140012"]
        with self.assertRaises(MasterDataError) as raised:
            import_kanagawa_master(self.connection, self.write_master(older))

        self.assertIn("2026-08-28", str(raised.exception))
        self.assertIn("2026-08-29", str(raised.exception))
        self.assertEqual(self.database_snapshot(), before)

    def test_same_date_same_content_is_idempotent_and_hash_is_recorded(self) -> None:
        same = copy.deepcopy(load_master())
        before = self.database_snapshot()

        self.assertEqual(
            import_kanagawa_master(self.connection, self.write_master(same)),
            (45, 11),
        )
        self.assertEqual(self.database_snapshot(), before)
        stored_hash = self.connection.execute(
            "SELECT content_sha256 FROM master_imports"
        ).fetchone()[0]
        self.assertEqual(len(stored_hash), 64)

    def test_json_and_array_order_do_not_change_same_date_content_hash(self) -> None:
        reordered = copy.deepcopy(load_master())
        reordered["areas"] = [
            dict(reversed(list(area.items())))
            for area in reversed(reordered["areas"])
        ]
        reordered["stations"] = [
            {
                **dict(reversed(list(station.items()))),
                "area_codes": list(reversed(station["area_codes"])),
            }
            for station in reversed(reordered["stations"])
        ]
        reordered = dict(reversed(list(reordered.items())))
        before = self.database_snapshot()

        import_kanagawa_master(
            self.connection, self.write_master(reordered, indent=4)
        )
        self.assertEqual(self.database_snapshot(), before)

    def test_same_date_changed_content_is_rejected_without_changes(self) -> None:
        conflicting = copy.deepcopy(load_master())
        self.station(conflicting, "46106")["name"] = "競合する地点名"
        self.station(conflicting, "46106")["longitude"] = 139.7
        before = self.database_snapshot()

        with self.assertRaisesRegex(MasterDataError, "同じ確認日"):
            import_kanagawa_master(
                self.connection, self.write_master(conflicting)
            )
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_area_is_ended_but_forecast_history_remains(self) -> None:
        provider_id = self.connection.execute(
            "SELECT id FROM providers WHERE code = 'JMA'"
        ).fetchone()[0]
        area_id = self.connection.execute(
            "SELECT id FROM forecast_areas WHERE area_code = '1420300'"
        ).fetchone()[0]
        with self.connection:
            run_id = self.connection.execute(
                """
                INSERT INTO forecast_runs
                  (provider_id, fetched_at, issued_at, source_url,
                   content_sha256, status)
                VALUES (?, '2026-08-28T00:00:00Z', '2026-08-27T23:00:00Z',
                        'https://example.invalid', 'missing-area-history', 'completed')
                """,
                (provider_id,),
            ).lastrowid
            self.connection.execute(
                """
                INSERT INTO forecasts
                  (forecast_run_id, forecast_area_id, target_start, target_end,
                   forecast_type)
                VALUES (?, ?, '2026-08-29T00:00:00Z',
                        '2026-08-30T00:00:00Z', 'daily')
                """,
                (run_id, area_id),
            )

        changed = copy.deepcopy(load_master())
        changed["verified_at"] = "2026-08-28"
        changed["areas"] = [
            area for area in changed["areas"] if area["area_code"] != "1420300"
        ]
        path = self.write_master(changed)
        import_kanagawa_master(self.connection, path)

        ended = self.connection.execute(
            """
            SELECT valid_to, master_verified_at
            FROM forecast_areas
            WHERE area_code = '1420300'
            """
        ).fetchone()
        self.assertEqual(tuple(ended), ("2026-08-28", "2026-08-28"))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM forecast_areas WHERE valid_to IS NULL"
            ).fetchone()[0],
            44,
        )
        self.assertIsNone(
            self.connection.execute(
                """
                SELECT child.area_code
                FROM forecast_areas AS child
                WHERE child.valid_to IS NULL AND child.area_code = '1420300'
                """
            ).fetchone()
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT forecast_area_id FROM forecasts WHERE id = 1"
            ).fetchone()[0],
            area_id,
        )
        before_reimport = self.database_snapshot()
        import_kanagawa_master(self.connection, path)
        self.assertEqual(self.database_snapshot(), before_reimport)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_area_end_before_or_on_start_rolls_back_entire_import(self) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE forecast_areas SET valid_from = '2026-08-28' WHERE area_code = '1420300'"
            )
        changed = copy.deepcopy(load_master())
        changed["verified_at"] = "2026-08-28"
        changed["areas"] = [
            area for area in changed["areas"] if area["area_code"] != "1420300"
        ]
        before = self.database_snapshot()

        with self.assertRaisesRegex(MasterDataError, "地域の終了日"):
            import_kanagawa_master(self.connection, self.write_master(changed))
        self.assertEqual(self.database_snapshot(), before)

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

    def test_retired_station_without_active_from_keeps_null_and_has_no_membership(self) -> None:
        changed = copy.deepcopy(load_master())
        changed["verified_at"] = "2026-08-29"
        retired = copy.deepcopy(changed["stations"][0])
        retired["station_code"] = "99999"
        retired["name"] = "廃止済み地点"
        retired.pop("active_from", None)
        retired["active_to"] = "2026-08-28"
        changed["stations"].append(retired)

        import_kanagawa_master(self.connection, self.write_master(changed))
        row = self.connection.execute(
            """
            SELECT id, active_from, active_to
            FROM observation_stations
            WHERE station_code = '99999'
            """
        ).fetchone()
        self.assertIsNone(row["active_from"])
        self.assertEqual(row["active_to"], "2026-08-28")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM station_area_memberships WHERE station_id = ?",
                (row["id"],),
            ).fetchone()[0],
            0,
        )

    def test_retired_station_closes_located_in_and_can_revive(self) -> None:
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

        retired = copy.deepcopy(load_master())
        retired["verified_at"] = "2026-08-29"
        station = self.station(retired, "46106")
        station.pop("active_from", None)
        station["active_to"] = "2026-08-28"
        import_kanagawa_master(self.connection, self.write_master(retired))

        station_period = self.connection.execute(
            """
            SELECT active_from, active_to
            FROM observation_stations WHERE station_code = '46106'
            """
        ).fetchone()
        self.assertEqual(tuple(station_period), ("2026-08-27", "2026-08-28"))
        relations = dict(
            self.connection.execute(
                """
                SELECT relation_type, valid_to
                FROM station_area_memberships
                WHERE station_id = ? AND valid_to IS NULL
                """,
                (station_id,),
            ).fetchall()
        )
        self.assertNotIn("located_in", relations)
        self.assertIsNone(relations["verification_target"])
        self.assertEqual(
            self.membership_rows("46106"),
            [("140011", "2026-08-27", "2026-08-28")],
        )

        revived = copy.deepcopy(load_master())
        revived["verified_at"] = "2026-08-30"
        revived_station = self.station(revived, "46106")
        revived_station.pop("active_from", None)
        revived_station.pop("active_to", None)
        import_kanagawa_master(self.connection, self.write_master(revived))

        station_period = self.connection.execute(
            """
            SELECT active_from, active_to
            FROM observation_stations WHERE station_code = '46106'
            """
        ).fetchone()
        self.assertEqual(tuple(station_period), ("2026-08-30", None))
        self.assertEqual(
            self.membership_rows("46106"),
            [
                ("140011", "2026-08-27", "2026-08-28"),
                ("140011", "2026-08-30", None),
            ],
        )
        verification_target = self.connection.execute(
            """
            SELECT valid_to FROM station_area_memberships
            WHERE station_id = ? AND relation_type = 'verification_target'
            """,
            (station_id,),
        ).fetchone()[0]
        self.assertIsNone(verification_target)
        self.assertEqual(self.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_invalid_membership_end_rolls_back_retired_station(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE station_area_memberships
                SET valid_from = '2026-08-28'
                WHERE station_id = (
                    SELECT id FROM observation_stations WHERE station_code = '46106'
                ) AND relation_type = 'located_in'
                """
            )
        retired = copy.deepcopy(load_master())
        retired["verified_at"] = "2026-08-29"
        self.station(retired, "46106")["active_to"] = "2026-08-28"
        before = self.database_snapshot()

        with self.assertRaisesRegex(MasterDataError, "関係の終了日"):
            import_kanagawa_master(self.connection, self.write_master(retired))
        self.assertEqual(self.database_snapshot(), before)


class MasterValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="weatherdb-invalid-"
        )
        temporary_path = Path(self.temporary_directory.name)
        self.db_path = temporary_path / "invalid.sqlite3"
        self.master_path = temporary_path / "invalid.json"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

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

    def test_extreme_and_non_finite_station_numbers_are_rejected(self) -> None:
        cases = (
            ("huge-latitude", "latitude", 10**400),
            ("huge-longitude", "longitude", -(10**400)),
            ("huge-elevation", "elevation", 10**400),
            ("nan-latitude", "latitude", float("nan")),
            ("positive-infinity", "elevation", float("inf")),
            ("negative-infinity", "elevation", float("-inf")),
            ("boolean-elevation", "elevation", False),
            ("latitude-below-range", "latitude", -91),
            ("longitude-above-range", "longitude", 181),
        )
        for label, field, value in cases:
            with self.subTest(label=label):
                invalid = copy.deepcopy(load_master())
                invalid["stations"][0][field] = value
                with self.assertRaises(MasterDataError):
                    validate_master(invalid)

    def test_cli_extreme_number_returns_one_without_traceback(self) -> None:
        initialize(self.db_path)
        invalid = copy.deepcopy(load_master())
        invalid["stations"][0]["elevation"] = 10**400
        self.master_path.write_text(
            json.dumps(invalid, ensure_ascii=False), encoding="utf-8"
        )
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
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="weatherdb-period-"
        )
        self.db_path = Path(self.temporary_directory.name) / "period.sqlite3"
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
        self.temporary_directory.cleanup()

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
