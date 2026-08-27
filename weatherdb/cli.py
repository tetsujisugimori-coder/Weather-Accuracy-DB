"""Command-line interface for Phase 1."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from weatherdb.config import database_path
from weatherdb.db import connect, database_status, initialize
from weatherdb.importers.areas import MasterDataError, import_kanagawa_master


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m weatherdb",
        description="気象庁の予報精度検証DB（Phase 1）",
        epilog=(
            "DB既定値は実行時カレントディレクトリの data/weather.sqlite3。"
            "優先順位は --db、WEATHERDB_DB_PATH、既定値です。"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=database_path(),
        help=(
            "SQLiteファイル（未指定時は WEATHERDB_DB_PATH、さらに未指定なら"
            "実行時カレントディレクトリの data/weather.sqlite3）"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="DBスキーマとJMA providerを初期化")
    import_parser = subparsers.add_parser(
        "import-areas", help="神奈川県の地域・観測地点マスタを登録"
    )
    import_parser.add_argument(
        "--master",
        type=Path,
        help="検証・再取込用のマスタJSON（未指定時はwheel同梱リソース）",
    )
    subparsers.add_parser("status", help="DB状態を表示")
    return parser


def _print_status(status: dict[str, object]) -> None:
    labels = (
        ("DBファイル", "path"),
        ("provider数", "providers"),
        ("登録地域数", "forecast_areas"),
        ("観測地点数", "observation_stations"),
        ("forecast run数", "forecast_runs"),
        ("forecast件数", "forecasts"),
        ("observation件数", "observations"),
        ("最新予報発表日時", "latest_issued_at"),
        ("最新予報取得日時", "latest_fetched_at"),
        ("最新観測日時", "latest_observed_at"),
        ("最後の取得status", "last_run_status"),
        ("foreign_keys", "foreign_keys"),
        ("journal_mode", "journal_mode"),
    )
    for label, key in labels:
        value = status[key]
        print(f"{label}: {value if value is not None else '-'}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path: Path = args.db.resolve()
    try:
        if args.command == "init":
            initialize(path)
            print(f"DBを初期化しました: {path}")
        elif args.command == "import-areas":
            if not path.exists():
                raise FileNotFoundError("DBがありません。先に init を実行してください")
            connection = connect(path)
            try:
                areas, stations = import_kanagawa_master(connection, args.master)
            finally:
                connection.close()
            print(f"地域・観測地点マスタを登録しました: 地域 {areas}件 / 観測地点 {stations}件")
        elif args.command == "status":
            if not path.exists():
                raise FileNotFoundError("DBがありません。先に init を実行してください")
            _print_status(database_status(path))
        return 0
    except (OSError, sqlite3.Error, MasterDataError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
