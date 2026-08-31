from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from weatherdb.cli import main
from weatherdb.db import SchemaCompatibilityError, connect, database_status, initialize
from weatherdb.forecast_fetch import (
    ForecastFetchError,
    fetch_and_store_forecast,
    fetch_bytes,
    save_raw_bytes,
)
from weatherdb.forecast_parser import (
    ForecastDataError,
    ForecastRecord,
    TEMPERATURE_DAILY,
    decode_forecast_json,
    identify_documents,
    parse_document,
)
from weatherdb.importers.areas import import_kanagawa_master

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def documents(*names: str) -> bytes:
    merged = []
    for name in names:
        merged.extend(json.loads(fixture(name)))
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":")).encode()


class FakeResponse:
    status = 200

    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.data


class Phase2Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="weatherdb-phase2-")
        self.root = Path(self.temporary_directory.name)
        self.db_path = self.root / "weather.sqlite3"
        self.raw_dir = self.root / "raw"
        initialize(self.db_path)
        self.connection = connect(self.db_path)
        import_kanagawa_master(self.connection)
        self.known_areas = {r[0] for r in self.connection.execute("SELECT area_code FROM forecast_areas")}
        self.known_stations = {r[0] for r in self.connection.execute("SELECT station_code FROM observation_stations")}

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def run_fetch(self, raw: bytes, when: str = "2026-08-29T03:00:00+00:00"):
        return fetch_and_store_forecast(
            self.connection,
            "https://example.invalid/140000.json",
            self.raw_dir,
            2.0,
            http_get=lambda _url, _timeout: raw,
            now=lambda: datetime.fromisoformat(when),
        )


class ParserTests(Phase2Base):
    def parsed(self, name: str):
        document = identify_documents(decode_forecast_json(fixture(name)))[0]
        return document, parse_document(document, self.known_areas, self.known_stations)

    def test_normal_short_and_weekly_parse(self) -> None:
        short, short_records = self.parsed("forecast_short_0500.json")
        weekly, weekly_records = self.parsed("forecast_weekly.json")
        self.assertEqual(short.document_type, "short_term")
        self.assertEqual(short.issued_at, "2026-08-28T20:00:00+00:00")
        self.assertEqual(weekly.document_type, "weekly")
        self.assertTrue(short_records)
        self.assertTrue(weekly_records)

    def test_document_order_is_not_significant(self) -> None:
        raw = documents("forecast_weekly.json", "forecast_short_0500.json")
        found = identify_documents(decode_forecast_json(raw))
        self.assertEqual([d.document_type for d in found], ["weekly", "short_term"])

    def test_0500_1100_1700_temperature_rules(self) -> None:
        expectations = {
            "forecast_short_0500.json": (4, 32.0, 25.0),
            "forecast_short_1100.json": (4, 33.0, None),
            "forecast_short_1700.json": (2, 31.0, 24.0),
        }
        for name, (count, high, low) in expectations.items():
            with self.subTest(name=name):
                _, records = self.parsed(name)
                temps = [r for r in records if r.forecast_type == TEMPERATURE_DAILY]
                self.assertEqual(len(temps), count)
                yokohama = next(r for r in temps if r.target_code == "46106")
                if name == "forecast_short_0500.json":
                    yokohama = next(
                        r for r in temps
                        if r.target_code == "46106" and r.target_start == "2026-08-28T15:00:00+00:00"
                    )
                self.assertEqual(yokohama.high_temperature, high)
                self.assertEqual(yokohama.low_temperature, low)

    def test_targets_and_periods_are_not_inferred(self) -> None:
        _, records = self.parsed("forecast_short_0500.json")
        weather = next(r for r in records if r.weather_code == "101")
        pop = next(r for r in records if r.precipitation_probability == 0)
        temp = next(r for r in records if r.target_code == "46106" and r.high_temperature == 32.0)
        self.assertEqual((weather.target_kind, weather.target_code), ("area", "140010"))
        self.assertEqual((temp.target_kind, temp.target_code), ("station", "46106"))
        self.assertEqual(pop.target_end, "2026-08-29T03:00:00+00:00")
        self.assertEqual(weather.target_start, "2026-08-28T15:00:00+00:00")
        self.assertEqual(weather.target_end, "2026-08-29T15:00:00+00:00")
        self.assertEqual(temp.target_start, "2026-08-28T15:00:00+00:00")
        self.assertEqual(temp.target_end, "2026-08-29T15:00:00+00:00")

    def test_unknown_codes_missing_key_length_probability_and_datetime(self) -> None:
        cases = []
        base = json.loads(fixture("forecast_short_0500.json"))
        unknown_area = json.loads(json.dumps(base))
        unknown_area[0]["timeSeries"][0]["areas"][0]["area"]["code"] = "999999"
        cases.append(("unknown-area", unknown_area, "未知の予報区域"))
        unknown_station = json.loads(json.dumps(base))
        unknown_station[0]["timeSeries"][2]["areas"][0]["area"]["code"] = "99999"
        cases.append(("unknown-station", unknown_station, "未知の観測地点"))
        missing = json.loads(json.dumps(base))
        missing[0].pop("publishingOffice")
        cases.append(("missing", missing, "publishingOffice"))
        mismatch = json.loads(json.dumps(base))
        mismatch[0]["timeSeries"][1]["areas"][0]["pops"].pop()
        cases.append(("length", mismatch, "配列長"))
        bad_pop = json.loads(json.dumps(base))
        bad_pop[0]["timeSeries"][1]["areas"][0]["pops"][0] = "110"
        cases.append(("probability", bad_pop, "0から100"))
        bad_date = json.loads(json.dumps(base))
        bad_date[0]["reportDatetime"] = "invalid"
        cases.append(("datetime", bad_date, "reportDatetime"))
        for label, value, message in cases:
            with self.subTest(label=label):
                raw = json.dumps(value).encode()
                with self.assertRaisesRegex(ForecastDataError, message):
                    docs = identify_documents(decode_forecast_json(raw))
                    parse_document(docs[0], self.known_areas, self.known_stations)

    def test_json_decode_error(self) -> None:
        with self.assertRaisesRegex(ForecastDataError, "decode"):
            decode_forecast_json(b"{broken")


class DatabaseAndFetchTests(Phase2Base):
    def test_schema_target_check_and_partial_unique_indexes(self) -> None:
        provider_id = self.connection.execute("SELECT id FROM providers WHERE code='JMA'").fetchone()[0]
        area_id = self.connection.execute("SELECT id FROM forecast_areas WHERE area_code='140010'").fetchone()[0]
        station_id = self.connection.execute("SELECT id FROM observation_stations WHERE station_code='46106'").fetchone()[0]
        with self.connection:
            run_id = self.connection.execute(
                """INSERT INTO forecast_runs
                   (provider_id,fetched_at,issued_at,document_type,source_url,document_sha256,status)
                   VALUES (?,'2026-08-29T00:00:00+00:00','2026-08-28T20:00:00+00:00',
                           'short_term','x','schema-doc','completed')""", (provider_id,)
            ).lastrowid
        sql = """INSERT INTO forecasts
                 (forecast_run_id,forecast_area_id,station_id,target_start,target_end,forecast_type)
                 VALUES (?,?,?,?,?,'weather_daily')"""
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(sql, (run_id, None, None, "2026-08-29T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(sql, (run_id, area_id, station_id, "2026-08-29T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))
        self.connection.execute(sql, (run_id, area_id, None, "2026-08-29T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(sql, (run_id, area_id, None, "2026-08-29T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))
        station_sql = sql.replace("'weather_daily'", "'temperature_daily'")
        self.connection.execute(station_sql, (run_id, None, station_id, "2026-08-29T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(station_sql, (run_id, None, station_id, "2026-08-29T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))

    def test_fetch_saves_raw_runs_forecasts_and_separate_timestamps(self) -> None:
        raw = documents("forecast_short_0500.json", "forecast_weekly.json")
        result = self.run_fetch(raw)
        self.assertEqual((result.document_count, result.completed_runs, result.skipped_documents), (2, 2, 0))
        self.assertEqual(result.raw_file_path.read_bytes(), raw)
        runs = self.connection.execute(
            "SELECT issued_at,fetched_at,document_type,status,raw_file_sha256,document_sha256 FROM forecast_runs ORDER BY document_type"
        ).fetchall()
        self.assertEqual({r[2] for r in runs}, {"short_term", "weekly"})
        self.assertTrue(all(r[1] == "2026-08-29T03:00:00+00:00" for r in runs))
        self.assertTrue(all(r[0] != r[1] for r in runs))
        self.assertTrue(all(r[3] == "completed" and len(r[4]) == 64 and len(r[5]) == 64 for r in runs))
        area_and_station = self.connection.execute(
            "SELECT SUM(forecast_area_id IS NOT NULL),SUM(station_id IS NOT NULL) FROM forecasts"
        ).fetchone()
        self.assertGreater(area_and_station[0], 0)
        self.assertGreater(area_and_station[1], 0)

    def test_duplicate_document_skips_but_new_issue_preserves_history(self) -> None:
        first = self.run_fetch(fixture("forecast_short_0500.json"))
        duplicate = self.run_fetch(fixture("forecast_short_0500.json"), "2026-08-29T04:00:00+00:00")
        before = self.connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
        changed = self.run_fetch(fixture("forecast_short_1100.json"), "2026-08-29T05:00:00+00:00")
        after = self.connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
        self.assertGreater(first.forecast_count, 0)
        self.assertEqual((duplicate.completed_runs, duplicate.skipped_documents, duplicate.forecast_count), (0, 1, 0))
        self.assertEqual(changed.completed_runs, 1)
        self.assertGreater(after, before)
        same_day = self.connection.execute(
            """SELECT COUNT(DISTINCT r.issued_at) FROM forecasts f JOIN forecast_runs r ON r.id=f.forecast_run_id
               WHERE f.target_start='2026-08-28T15:00:00+00:00'"""
        ).fetchone()[0]
        self.assertEqual(same_day, 2)

    def test_parser_failure_creates_failed_run_without_forecasts(self) -> None:
        broken = json.loads(fixture("forecast_short_0500.json"))
        broken[0]["timeSeries"][1]["areas"][0]["pops"][0] = "999"
        with self.assertRaises(ForecastFetchError):
            self.run_fetch(json.dumps(broken).encode())
        run = self.connection.execute("SELECT status,error_message FROM forecast_runs ORDER BY id DESC").fetchone()
        self.assertEqual(run[0], "failed")
        self.assertIn("0から100", run[1])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0], 0)

    def test_db_failure_rolls_back_partial_forecasts_and_marks_failed(self) -> None:
        original = identify_documents(decode_forecast_json(fixture("forecast_short_1700.json")))[0]
        duplicate = ForecastRecord(
            "140010", "area", "2026-08-29T15:00:00+00:00", "2026-08-30T15:00:00+00:00", "weather_daily", weather_code="101"
        )
        with patch("weatherdb.forecast_fetch.parse_document", return_value=[duplicate, duplicate]):
            with self.assertRaises(ForecastFetchError):
                self.run_fetch(fixture("forecast_short_1700.json"))
        run = self.connection.execute("SELECT status FROM forecast_runs WHERE document_sha256=?", (original.sha256,)).fetchone()
        self.assertEqual(run[0], "failed")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0], 0)

    def test_decode_failure_creates_bounded_failed_run(self) -> None:
        with self.assertRaises(ForecastDataError):
            self.run_fetch(b"{broken")
        row = self.connection.execute("SELECT status,issued_at,document_type,error_message FROM forecast_runs").fetchone()
        self.assertEqual(tuple(row[:3]), ("failed", None, None))
        self.assertLessEqual(len(row[3]), 500)

    def test_status_reports_latest_run_and_error(self) -> None:
        with self.assertRaises(ForecastDataError):
            self.run_fetch(b"bad")
        status = database_status(self.db_path)
        self.assertEqual(status["forecast_runs"], 1)
        self.assertEqual(status["last_run_status"], "failed")
        self.assertIn("decode", status["last_run_error"])


class HttpRawAndCliTests(Phase2Base):
    def test_http_timeout_status_and_empty_response(self) -> None:
        with patch("weatherdb.forecast_fetch.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaisesRegex(ForecastFetchError, "接続"):
                fetch_bytes("https://example.invalid", 1)
        error = HTTPError("https://example.invalid", 503, "Unavailable", {}, None)
        with patch("weatherdb.forecast_fetch.urlopen", side_effect=error):
            with self.assertRaisesRegex(ForecastFetchError, "503"):
                fetch_bytes("https://example.invalid", 1)
        with patch("weatherdb.forecast_fetch.urlopen", return_value=FakeResponse(b"")):
            with self.assertRaisesRegex(ForecastFetchError, "空"):
                fetch_bytes("https://example.invalid", 1)

    def test_raw_write_failure(self) -> None:
        blocker = self.root / "not-a-directory"
        blocker.write_text("x")
        with self.assertRaisesRegex(ForecastFetchError, "raw JSON"):
            save_raw_bytes(b"[]", blocker, datetime.now(timezone.utc), "140000")

    def test_cli_success_failure_and_no_traceback(self) -> None:
        self.connection.close()
        stdout = io.StringIO()
        with patch("weatherdb.forecast_fetch.urlopen", return_value=FakeResponse(fixture("forecast_short_1700.json"))):
            with contextlib.redirect_stdout(stdout):
                success = main(["--db", str(self.db_path), "fetch-forecast", "--raw-dir", str(self.raw_dir)])
        self.assertEqual(success, 0)
        self.assertIn("completed run数: 1", stdout.getvalue())
        stderr = io.StringIO()
        with patch("weatherdb.forecast_fetch.urlopen", side_effect=TimeoutError("timed out")):
            with contextlib.redirect_stderr(stderr):
                failure = main(["--db", str(self.db_path), "fetch-forecast", "--raw-dir", str(self.raw_dir)])
        self.assertEqual(failure, 1)
        self.assertIn("エラー:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.connection = connect(self.db_path)

    def test_missing_db_and_master_are_clear_errors(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(["--db", str(self.root / "missing.sqlite3"), "fetch-forecast"])
        self.assertEqual(result, 1)
        self.assertIn("init", stderr.getvalue())
        empty_db = self.root / "empty.sqlite3"
        initialize(empty_db)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(["--db", str(empty_db), "fetch-forecast"])
        self.assertEqual(result, 1)
        self.assertIn("import-areas", stderr.getvalue())


class OldSchemaTests(unittest.TestCase):
    def test_old_phase1_schema_is_not_modified_and_has_safe_guidance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="weatherdb-old-") as directory:
            path = Path(directory) / "old.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE forecast_runs (id INTEGER PRIMARY KEY, fetched_at TEXT);
                CREATE TABLE forecasts (id INTEGER PRIMARY KEY, forecast_area_id INTEGER NOT NULL);
                """
            )
            connection.close()
            before = path.read_bytes()
            with self.assertRaisesRegex(SchemaCompatibilityError, "バックアップ"):
                initialize(path)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
