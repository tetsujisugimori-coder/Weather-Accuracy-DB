# Weather-Accuracy-DB

気象庁が発表した過去の予報と、その後の観測値をSQLiteへ蓄積し、神奈川県全域の予報精度を後から検証するためのCLIプロトタイプです。現在の天気を表示するアプリではありません。

現在は **Phase 2まで完成** しています。Phase 1の地域・地点マスタに加え、神奈川県の府県天気予報取得、元JSON保存、短期・週間parser、発表回ごとの履歴、`forecast_runs`の状態管理、`fetch-forecast`を実装しています。観測値取得と精度分析はまだ実装していません。

## 要件とリポジトリからの実行

- Python 3.11以上
- SQLite（Python標準の `sqlite3` を使用）
- 実行時の外部Pythonパッケージ不要（HTTP・JSON・SQLiteは標準ライブラリ）

```powershell
cd C:\Users\tetsu\Documents\Codex\Weather-Accuracy-DB
python -m weatherdb init
python -m weatherdb import-areas
python -m weatherdb fetch-forecast
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
python -m pip install .\dist\weather_accuracy_db-0.2.0-py3-none-any.whl

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

Phase 1のマスタは`verified_at`時点のスナップショットであり、将来予定の開始日・終了日は扱いません。地域の`valid_from`・`valid_to`と地点の`active_from`・`active_to`は、値を指定する場合はすべて`verified_at`以前である必要があります。これにより、`valid_to IS NULL` / `active_to IS NULL`を現役とするインポーターとSQLの判定を一致させます。

マスタから消えた現役地域は物理削除せず、今回の`verified_at`を`valid_to`として終了します。消えた現役関係も同様に履歴を残し、新しい関係は`valid_from`付きで追加します。一度終了した地点が復活する場合、開始日はマスタの`active_from`、未指定なら`verified_at`とし、地点の`active_from`と新しい`located_in.valid_from`の両方に同じ日を使います。期間は半開区間として扱うため直前の`active_to`と同日の復活は許可しますが、それより前の開始日は拒否します。`active_to`を持つ廃止地点は、その`active_to`と同じ日付で現役`located_in`を閉じます。開始・終了の前後関係が逆転する入力は日付を補正や上書きせず、マスタ取込全体をロールバックして拒否します。`verification_target`はこの同期の対象外です。

## Phase 2: 予報取得

通常利用では、気象庁公式の神奈川県 `140000` を取得します。

```powershell
python -m weatherdb --db .\data\weather.sqlite3 fetch-forecast
```

既定URLは <https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json>、既定timeoutは20秒、raw保存先は実行時カレントディレクトリの `raw/jma/forecasts/` です。検証時だけ `--url`、`--raw-dir`、`--timeout` を変更できます。User-Agentを明示し、timeout、HTTP status、接続、空レスポンス、decode、構造エラーを通常エラーとして扱います。

成功時はraw保存先、処理文書数、completed run数、保存forecast数、重複skip文書数を表示します。全文書がcompleted済みだった場合、未参照の新規rawを削除し、raw保存先には削除したファイルパスではなく`-`を表示します。通信・JSON・DB・raw整理の通常エラーは終了コード1で、Tracebackは表示しません。

### 短期・週間文書と予報対象

1回のHTTPレスポンスは、現在は短期予報と週間予報を含みます。配列の0番・1番ではなく、各文書の`timeSeries`にある要素構造で識別します。文書ごとの`reportDatetime`を各runの`issued_at`とし、HTTP取得時刻`fetched_at`とは分離します。

- 短期の天気・6時間降水確率は一次細分区域（東部`140010`、西部`140020`）へ保存する。
- 週間の天気・降水確率はJSONどおり神奈川県`140000`へ保存する。
- 短期気温は横浜`46106`、小田原`46166`、週間気温は横浜`46106`など、JSONにある観測地点へ保存する。
- 気温地点を名称や配列位置から東部・西部へ変換しない。未知コードは構造エラーにする。

`forecasts.forecast_area_id`と`station_id`は排他的です。区域予報では前者だけ、地点気温では後者だけを設定し、両方設定または両方NULLはCHECK制約で拒否します。区域・地点それぞれに部分UNIQUE INDEXを置き、NULLを含むSQLiteの通常UNIQUEだけには依存しません。

### 時刻と予報期間

`issued_at`、`fetched_at`、`target_start`、`target_end`はUTCのoffset付きISO 8601（`+00:00`）へ正規化します。予報日と日付境界は日本時間として解釈します。

- 日別天気と日別気温: 日本時間00:00から翌日00:00まで。
- 短期降水確率: JSONの`timeDefines`から6時間後まで。
- 週間降水確率: JSONが表す日別期間（日本時間の1日）。
- 短期気温: 日本時間00:00の値を最低、09:00の値を最高とし、配列位置ではなくtimestampで分類する。別時刻や同日同種の重複は推測せずエラーにする。不提供値はNULLにする。

Windowsの最小Python環境にIANA timezone DBがない場合は、日本の予報期間についてUTC+09:00固定offsetを`Asia/Tokyo`相当として使います（日本は対象期間にDSTを採用していません）。

### raw JSONとSHA-256

HTTPレスポンスの元bytesを整形し直さず、一時ファイルへ書いて`fsync`した後に置換します。ファイル名にはUTC取得日時、`140000`、`forecast`、raw hashの一部を含めます。SQLiteへJSON本体は入れません。

`raw_file_path`は、`--raw-dir`が任意の場所を指せ、別のカレントディレクトリからも一意に解決できるよう絶対パスで保存します。`raw_file_sha256`はHTTPレスポンス全体の元bytes、`document_sha256`は短期または週間の文書をキー順に正規化したJSONのSHA-256です。Phase 1の`content_sha256`は旧テスト・手動データとの互換列で、Phase 2取得処理は使用しません。

同じ`document_sha256`で`completed`のrunがある場合だけskipします。failed runや異常終了で残ったstarted runは監査履歴として維持し、新しいrunで再試行します。再試行が成功した後の取得はskipされ、completed runはprovider・文書hashごとに1件だけです。一方、対象日が同じでも発表日時または文書内容が変われば別runとして追加し、過去予報をUPDATEしません。

rawはdecodeと構造エラーの証跡を残すため、文書判定より先に保存します。新規・再試行・failedのrunが1件でも参照するrawは保持し、短期・週間の一方だけが新規の場合も共通rawを新規runから参照します。全文書がcompleted済みで新規rawを参照するrunがない場合だけ、そのrawを削除します。削除に失敗した場合は、存在するrawをfailed runから参照してエラーを明示し、未管理ファイルとして無言で残しません。

### run状態と失敗時transaction

文書を識別できたら`started`を記録し、その文書の全forecast INSERTと`completed`への更新を1transactionで実行します。1件でも失敗すればforecast transactionをrollbackし、別transactionでrunを`failed`へ更新して短い`error_message`を残します。decode以前など文書種別・発表日時を特定できない失敗は、両列NULLのfailed runとして記録します。

failed・startedはcompletedの一意性対象外なので、新しい取得は別runとして再試行できます。過去runのstatusや`error_message`は変更しません。同時実行ではcompletedへの更新前にも既存completedを確認し、`status='completed'`限定の部分UNIQUE INDEXを最終防御にします。別処理が先に完了した場合、負けた処理のforecast transactionを全rollbackして重複skipとして扱うため、部分的なforecastや複数completedを残しません。

`status`の「最新保存run取得日時」は、`forecast_runs`へ保存された最新`fetched_at`です。全文書重複でrunを作らなかったHTTP取得は含まず、「最後のHTTP試行時刻」ではありません。

## SQLite schema概要

- `providers`: データ提供元。Phase 1は `JMA` のみ。
- `forecast_areas`: 自己参照外部キーによる地域階層。
- `observation_stations`: 観測地点、座標、標高、地点種別、観測要素フラグ。
- `station_area_memberships`: 地点と予報区域の多対多関係。有効期間ごとに履歴を保持。
- `forecast_runs`: 文書単位。短期・週間種別、`issued_at`と`fetched_at`、raw/document SHA-256、状態を持つ。
- `forecasts`: 各runに属する区域天気・降水確率または地点日別気温。同じ対象日時でもrunが異なれば履歴として共存する。
- `observations`: 地点・観測日時ごとの生の降水量・気温。
- `master_imports`: どの確認日のマスタをimportしたかと、正規化内容のSHA-256を記録。

主キーはすべてSQLiteの整数キーです。JMAコードにはproviderとの複合UNIQUE制約を置きます。completed文書の再取込は`forecast_runs(provider_id, document_sha256) WHERE status='completed'`、同一run内の区域・地点予報重複はそれぞれの自然キー、観測重複は`(station_id, observed_at)`で防ぎます。

スキーマ全文はパッケージ内の [weatherdb/resources/schema.sql](weatherdb/resources/schema.sql)、Phase 1のSQL例は [sql/queries.sql](sql/queries.sql) を参照してください。

### 既存Phase 1 DBの扱い

Phase 2は`forecasts`の対象を区域・地点の排他構造へ変更し、`forecast_runs`へ文書種別と2種類のSHA-256を追加します。利用者の予報データがまだない段階であり、テーブル再構築を伴う自動マイグレーションは事故の方が大きいため実装していません。`init`と`fetch-forecast`は旧Phase 1スキーマを読み取り専用で検出し、変更せず明確なエラーにします。旧DBは削除せずバックアップへ移動して新規作成してください。

```powershell
Move-Item -LiteralPath .\data\weather.sqlite3 -Destination .\data\weather.phase1-backup.sqlite3
python -m weatherdb init
python -m weatherdb import-areas
python -m weatherdb status
```

必要なユーザーデータが入っている場合は削除せず、バックアップを保持してください。

PR #4初版のPhase 2 DBは列構成が同じため、バックアップ後に`init`を再実行すると、文書一意INDEXをcompleted限定へ安全に置き換え、run検索INDEXを追加します。`fetch-forecast`は古いINDEX構成を検出した場合、先に`init`を求めます。

## 時刻の扱い

DBへ保存する予報時刻はUTCのoffset付きISO 8601（例 `2026-08-27T08:00:00+00:00`）に正規化します。気象庁データの解釈と日付境界は`Asia/Tokyo`で行い、naive datetimeは拒否します。`created_at`はSQLiteのUTC時刻です。

## raw JSON方針

Phase 2では、取得時刻と種別を含む名前で`raw/jma/forecasts/`以下に元JSONを保存し、SQLiteには絶対パスとハッシュだけを記録します。parser変更時に再解析でき、巨大JSONをDBへ蓄積しません。`raw/`はGit管理対象外です。

## 降水確率と観測地点一致率

降水確率は、指定時間帯に区域内のある地点で1mm以上の降水がある確率です。長期的には予報確率帯ごとの実際の降水発生率でcalibrationを検証します。

観測地点一致率は、区域内の対象観測地点のうち予報した降水有無と一致した地点の割合です。降水確率とは意味が違うため「的中確率」という名称は使いません。判定結果やMAEだけを一次テーブルへ保存せず、予報値と観測値から後で計算します。

## SQLite設定とINDEX

各接続で `PRAGMA foreign_keys = ON` と5秒のbusy timeoutを設定します。WALは、将来の定期取得中にも読み取り分析しやすく、異常終了時の耐性も得やすいため採用しています。代わりに `-wal` / `-shm` ファイルが生じ、ネットワーク共有には向かないため、DBはローカルディスクで使ってください。

日時、予報区域、地域親子関係と中間テーブルの検索に必要なINDEXだけを定義しています。現役の地点―区域関係には`idx_station_memberships_active`部分UNIQUE INDEXを使います。区域・地点別のforecast自然キーINDEXは部分INDEXなので、`WHERE forecast_run_id = ?`だけの検索には利用できません。このため`idx_forecasts_run`を別に定義しています。`observations.station_id`は`UNIQUE(station_id, observed_at)`のSQLite自動INDEX先頭列を利用します。`sql/queries.sql`に`EXPLAIN QUERY PLAN`の例があります。

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

- 観測データはまだ取得しない。
- 地域・地点マスタは確認日のスナップショットで、将来予定日と気象庁サイトからの自動更新は扱わない。
- `located_in` は地理的対応で、公式な検証対象地点リストではない。
- 観測地点テーブル自体は1行/地点のため、廃止後に同じ地点が復活した場合の地点履歴全体は保持しない。地点―区域関係の期間履歴は保持する。
- 気象庁ホームページ表示用JSONはバージョン固定APIではないため、構造変更時はparser更新が必要。
- 週間降水確率は日別、短期降水確率は6時間別として同じ要素種別で期間により区別する。
- 日別観測値、予報との対応付け、降水判定、気温誤差、calibration、Web UIは未実装。

## 次のPhase

Phase 3では観測値取得、予報と実測の対応付け、降水分析、気温誤差分析、calibrationを実装予定です。Web UIはその後の対象です。
