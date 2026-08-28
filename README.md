# Weather-Accuracy-DB

気象庁が発表した過去の予報と、その後の観測値をSQLiteへ蓄積し、神奈川県全域の予報精度を後から検証するためのCLIプロトタイプです。現在の天気を表示するアプリではありません。

現在は **Phase 1のみ完成** しています。DBスキーマ、JMA provider、神奈川県の地域階層、観測地点マスタ、`init` / `import-areas` / `status` とそのテストを実装しています。予報・観測値のHTTP取得、parser、精度分析はまだ実装していません。

## 要件とリポジトリからの実行

- Python 3.11以上
- SQLite（Python標準の `sqlite3` を使用）
- Phase 1は外部Pythonパッケージ不要

```powershell
cd C:\Users\tetsu\Documents\Codex\Weather-Accuracy-DB
python -m weatherdb init
python -m weatherdb import-areas
python -m weatherdb status
```

別DBを使う場合は、サブコマンドより前に `--db` を指定します。

```powershell
python -m weatherdb --db .\data\practice.sqlite3 init
python -m weatherdb --db .\data\practice.sqlite3 import-areas
python -m weatherdb --db .\data\practice.sqlite3 status
```

環境変数 `WEATHERDB_DB_PATH` でも既定パスを変更できます。

## wheel・pip install後の実行

wheelには、Pythonコードに加えてDBスキーマSQLと神奈川県マスタJSONが含まれます。インストール後はリポジトリ外でもコンソールコマンドを実行できます。

wheelのビルド時だけは`pyproject.toml`に記載した`setuptools>=68`が必要です。これはビルド依存であり、インストール後の実行時依存ではありません。

```powershell
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
python -m pip install .\dist\weather_accuracy_db-0.1.0-py3-none-any.whl

New-Item -ItemType Directory -Path C:\weatherdb-work -Force
Set-Location C:\weatherdb-work
weatherdb init
weatherdb import-areas
weatherdb status
```

`pip install .` による直接インストールも可能です。Pythonパッケージ以外の実行時依存はありません。

DBパスの優先順位は次のとおりです。

1. CLIの `--db PATH`
2. 環境変数 `WEATHERDB_DB_PATH`
3. 実行時カレントディレクトリの `data/weather.sqlite3`

既定値はインストール先ではなく、コマンドを実行したディレクトリを基準にします。読み取り専用の [schema.sql](weatherdb/resources/schema.sql) と [kanagawa.json](weatherdb/resources/kanagawa.json) は`weatherdb.resources`内のパッケージリソースです。書き込み対象のSQLiteとは分離され、`site-packages`内へDBを作成しません。

## 対象地域と公式データ

地域・地点マスタは2026-08-27に以下の気象庁公式JSONと公式資料を確認して作成しました。詳細と注意事項は [docs/data-sources.md](docs/data-sources.md) にあります。

- 地域マスタ: <https://www.jma.go.jp/bosai/common/const/area.json>
- アメダス地点表: <https://www.jma.go.jp/bosai/amedas/const/amedastable.json>
- 神奈川県の府県天気予報: <https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json>
- 精度検証方法: <https://www.data.jma.go.jp/yoho/kensho/explanation.html>

神奈川県 `140000` の下に、一次細分区域の東部 `140010`・西部 `140020`、市町村等をまとめた7区域、35の市町村等区域を保存します。現行アメダス地点表の神奈川県内11地点も保存します。

## 予報区域と観測地点は別概念

予報は区域に対して発表され、観測は地点で行われます。`observation_stations.forecast_area_id` のような固定1対1列は置かず、`station_area_memberships` 中間テーブルで関係と有効期間を表現します。

現在登録している `located_in` は地理的な所属の初期対応です。気象庁の公式な精度検証対象地点を意味しません。将来、根拠を確認した検証対象を `verification_target` として別登録できます。

`import-areas`を新しいマスタで再実行すると、`verified_at`を境界日として地域・地点・`located_in`を同期します。マスタは`verified_at`の古い順に取り込み、取込済みの最新日より古いマスタは拒否します。同じ日付は、検証済みデータについてJSONオブジェクトのキー、地域・地点、各地点の`area_codes`を決定的な順序へ正規化したSHA-256が一致する場合だけ再取込できます。

マスタから消えた現役地域は物理削除せず、今回の`verified_at`を`valid_to`として終了します。消えた現役関係も同様に履歴を残し、新しい関係は`valid_from`付きで追加します。一度終了した地点が復活した場合は、地点の`active_from`と`located_in`の新しい期間を開始します。`active_to`を持つ廃止地点は、その`active_to`と同じ日付で現役`located_in`を閉じます。`verification_target`はこの同期の対象外です。

## SQLite schema概要

- `providers`: データ提供元。Phase 1は `JMA` のみ。
- `forecast_areas`: 自己参照外部キーによる地域階層。
- `observation_stations`: 観測地点、座標、標高、地点種別、観測要素フラグ。
- `station_area_memberships`: 地点と予報区域の多対多関係。有効期間ごとに履歴を保持。
- `forecast_runs`: 取得処理単位。`issued_at` と `fetched_at` を分離し、rawパスとSHA-256を持つ。
- `forecasts`: 各runに属する予報値。同じ対象日時でもrunが異なれば履歴として共存する。
- `observations`: 地点・観測日時ごとの生の降水量・気温。
- `master_imports`: どの確認日のマスタをimportしたかと、正規化内容のSHA-256を記録。

主キーはすべてSQLiteの整数キーです。JMAコードにはproviderとの複合UNIQUE制約を置きます。完全に同じraw予報の再取込は `forecast_runs(provider_id, content_sha256)`、同一run内の予報重複は予報の自然キー、観測重複は `(station_id, observed_at)` で防ぐ設計です。一方、発表回の異なる予報は別runなので上書きされません。

スキーマ全文はパッケージ内の [weatherdb/resources/schema.sql](weatherdb/resources/schema.sql)、Phase 1のSQL例は [sql/queries.sql](sql/queries.sql) を参照してください。

### 既存Phase 1 DBの再作成

この修正では`station_area_memberships`のUNIQUE制約・有効期間必須化・部分INDEX、`forecasts`の期間CHECKに加え、`master_imports.content_sha256`を追加しました。中途半端な自動マイグレーションは行いません。以前のPhase 1スキーマで作成したDBはバックアップへ移動して再作成してください。

```powershell
Move-Item -LiteralPath .\data\weather.sqlite3 -Destination .\data\weather.phase1-backup.sqlite3
python -m weatherdb init
python -m weatherdb import-areas
python -m weatherdb status
```

必要なユーザーデータが入っている場合は削除せず、バックアップを保持してください。

## 時刻の扱い

DBへ保存する時刻はUTCのoffset付きISO 8601（例 `2026-08-27T08:00:00Z`）に正規化する方針です。気象庁データの解釈と日付境界は `Asia/Tokyo` で行います。naive datetimeは保存しません。Phase 1で自動生成する `created_at` はSQLiteのUTC時刻です。変換処理はデータ取得を実装するPhase 2で追加します。

## raw JSON方針

Phase 2以降では、取得時刻と種別を含む名前で `raw/jma/` 以下に元JSONを保存し、SQLiteにはパスとハッシュだけを記録します。parser変更時に再解析でき、巨大JSONをDBへ蓄積しません。`raw/` はGit管理対象外です。

## 降水確率と観測地点一致率

降水確率は、指定時間帯に区域内のある地点で1mm以上の降水がある確率です。長期的には予報確率帯ごとの実際の降水発生率でcalibrationを検証します。

観測地点一致率は、区域内の対象観測地点のうち予報した降水有無と一致した地点の割合です。降水確率とは意味が違うため「的中確率」という名称は使いません。判定結果やMAEだけを一次テーブルへ保存せず、予報値と観測値から後で計算します。

## SQLite設定とINDEX

各接続で `PRAGMA foreign_keys = ON` と5秒のbusy timeoutを設定します。WALは、将来の定期取得中にも読み取り分析しやすく、異常終了時の耐性も得やすいため採用しています。代わりに `-wal` / `-shm` ファイルが生じ、ネットワーク共有には向かないため、DBはローカルディスクで使ってください。

日時、予報区域、地域親子関係と中間テーブルの検索に必要なINDEXだけを定義しています。現役の地点―区域関係には`idx_station_memberships_active`部分UNIQUE INDEXを使います。`forecasts.forecast_run_id` と `observations.station_id` は、それぞれ先頭列に含むUNIQUE制約のSQLite自動INDEXを利用するため、重複する単一列INDEXは作りません。`sql/queries.sql` に `EXPLAIN QUERY PLAN` の例があります。

## テストとDB検証

```powershell
python -m unittest discover -s tests -v
python -m weatherdb status
```

整合性は次でも確認できます。

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/weather.sqlite3'); print(c.execute('PRAGMA integrity_check').fetchall()); print(c.execute('PRAGMA foreign_key_check').fetchall())"
```

## 現在の制約

- Phase 1のため予報・観測データはまだ取得しない。
- 地域・地点マスタは確認日のスナップショットで、気象庁サイトからの自動更新は行わない。
- `located_in` は地理的対応で、公式な検証対象地点リストではない。
- 観測地点テーブル自体は1行/地点のため、廃止後に同じ地点が復活した場合の地点履歴全体は保持しない。地点―区域関係の期間履歴は保持する。
- 日別観測値、降水判定、気温誤差、calibrationは未実装。

## 次のPhase

Phase 2では気象庁府県天気予報の取得、raw JSON保存、`forecast_runs` の状態遷移、parser、予報履歴保存、fixtureを使ったHTTP・JSON・transactionテストを実装します。Phase 3以降で観測値取得、対応付け、降水・気温・calibration・リードタイム分析へ進みます。
