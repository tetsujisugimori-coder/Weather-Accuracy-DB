from __future__ import annotations

import contextlib
import io
import sqlite3
import unittest
import uuid
from pathlib import Path

from weatherdb.cli import main
from weatherdb.db import connect, database_status, initialize
from weatherdb.importers.areas import import_kanagawa_master


class Phase1DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        test_data = Path(__file__).resolve().parent / "testdata"
        self.db_path = test_data / f"weather-{uuid.uuid4().hex}.sqlite3"

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def initialize_and_import(self) -> sqlite3.Connection:
        initialize(self.db_path)
        connection = connect(self.db_path)
        import_kanagawa_master(connection)
        return connection

    def test_initialize_creates_database_and_provider(self) -> None:
        initialize(self.db_path)
        self.assertTrue(self.db_path.exists())
        connection = connect(self.db_path)
        try:
            provider = connection.execute(
                "SELECT code, name FROM providers"
            ).fetchone()
            self.assertEqual((provider["code"], provider["name"]), ("JMA", "気象庁"))
        finally:
            connection.close()

    def test_initialize_is_idempotent(self) -> None:
        initialize(self.db_path)
        initialize(self.db_path)
        connection = connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM providers").fetchone()[0], 1)
        finally:
            connection.close()

    def test_foreign_keys_are_enabled_on_every_project_connection(self) -> None:
        initialize(self.db_path)
        connection = connect(self.db_path)
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO forecast_areas
                      (provider_id, area_code, name, area_level, source_url, master_verified_at)
                    VALUES (999, 'x', 'x', 'prefecture', 'https://example.invalid', '2026-08-27')
                    """
                )
        finally:
            connection.close()

    def test_import_registers_full_hierarchy_and_stations(self) -> None:
        connection = self.initialize_and_import()
        try:
            levels = dict(
                connection.execute(
                    "SELECT area_level, COUNT(*) FROM forecast_areas GROUP BY area_level"
                ).fetchall()
            )
            self.assertEqual(
                levels,
                {"prefecture": 1, "primary": 2, "grouped_municipality": 7, "municipality": 35},
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_stations").fetchone()[0], 11)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM station_area_memberships").fetchone()[0], 11)

            eastern_parent = connection.execute(
                """
                SELECT parent.area_code
                FROM forecast_areas AS child
                JOIN forecast_areas AS parent ON parent.id = child.parent_area_id
                WHERE child.area_code = '140010'
                """
            ).fetchone()[0]
            self.assertEqual(eastern_parent, "140000")
        finally:
            connection.close()

    def test_master_import_is_idempotent(self) -> None:
        connection = self.initialize_and_import()
        try:
            first = import_kanagawa_master(connection)
            second = import_kanagawa_master(connection)
            self.assertEqual(first, (45, 11))
            self.assertEqual(second, first)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM master_imports").fetchone()[0], 1)
        finally:
            connection.close()

    def test_station_to_area_is_many_to_many_ready(self) -> None:
        connection = self.initialize_and_import()
        try:
            row = connection.execute(
                """
                SELECT station.name, area.name, membership.relation_type
                FROM station_area_memberships AS membership
                JOIN observation_stations AS station ON station.id = membership.station_id
                JOIN forecast_areas AS area ON area.id = membership.forecast_area_id
                WHERE station.station_code = '46106'
                """
            ).fetchone()
            self.assertEqual(tuple(row), ("横浜", "横浜・川崎", "located_in"))
        finally:
            connection.close()

    def test_schema_preserves_forecast_history_and_blocks_exact_duplicate(self) -> None:
        connection = self.initialize_and_import()
        try:
            provider_id = connection.execute("SELECT id FROM providers WHERE code='JMA'").fetchone()[0]
            area_id = connection.execute("SELECT id FROM forecast_areas WHERE area_code='140010'").fetchone()[0]
            with connection:
                first_run = connection.execute(
                    """
                    INSERT INTO forecast_runs
                      (provider_id, fetched_at, issued_at, source_url, content_sha256, status)
                    VALUES (?, ?, ?, ?, ?, 'completed')
                    """,
                    (provider_id, "2026-08-24T08:01:00Z", "2026-08-24T08:00:00Z", "https://example.invalid/1", "hash-1"),
                ).lastrowid
                second_run = connection.execute(
                    """
                    INSERT INTO forecast_runs
                      (provider_id, fetched_at, issued_at, source_url, content_sha256, status)
                    VALUES (?, ?, ?, ?, ?, 'completed')
                    """,
                    (provider_id, "2026-08-25T08:01:00Z", "2026-08-25T08:00:00Z", "https://example.invalid/2", "hash-2"),
                ).lastrowid
                values = (area_id, "2026-08-26T00:00:00Z", "2026-08-27T00:00:00Z", "daily", "100", "晴れ")
                connection.execute(
                    """
                    INSERT INTO forecasts
                      (forecast_run_id, forecast_area_id, target_start, target_end,
                       forecast_type, weather_code, weather_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (first_run, *values),
                )
                connection.execute(
                    """
                    INSERT INTO forecasts
                      (forecast_run_id, forecast_area_id, target_start, target_end,
                       forecast_type, weather_code, weather_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (second_run, *values),
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO forecasts
                      (forecast_run_id, forecast_area_id, target_start, target_end,
                       forecast_type, weather_code, weather_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (first_run, *values),
                )
        finally:
            connection.close()

    def test_status_reports_phase1_counts(self) -> None:
        connection = self.initialize_and_import()
        connection.close()
        status = database_status(self.db_path)
        self.assertEqual(status["providers"], 1)
        self.assertEqual(status["forecast_areas"], 45)
        self.assertEqual(status["observation_stations"], 11)
        self.assertEqual(status["foreign_keys"], 1)

    def test_cli_init_import_and_status(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--db", str(self.db_path), "init"]), 0)
            self.assertEqual(main(["--db", str(self.db_path), "import-areas"]), 0)
            self.assertEqual(main(["--db", str(self.db_path), "status"]), 0)
        rendered = output.getvalue()
        self.assertIn("登録地域数: 45", rendered)
        self.assertIn("観測地点数: 11", rendered)

    def test_integrity_and_foreign_key_checks(self) -> None:
        connection = self.initialize_and_import()
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
