# Change log

## 2026-08-28 — Phase 1 review fixes for commit 46c2086

### 修正した4項目

1. `schema.sql`と`kanagawa.json`を`weatherdb.resources`へ移し、`importlib.resources`で読むようにした。wheelのpackage data設定を追加し、既定DBを実行時カレントディレクトリの`data/weather.sqlite3`へ変更した。
2. `located_in`関係を期間履歴として同期するようにした。削除時はマスタの`verified_at`で終了し、追加・復活時は同日を開始日にした。消えた観測地点の`active_to`も更新し、`verification_target`は変更しない。
3. マスタのルート、確認日、sources、地域・地点レコード、必須文字列、親参照、area_codes、緯度・経度等を明示検証し、構造エラーを`MasterDataError`へ統一した。
4. `forecasts`へ`CHECK (target_end > target_start)`を追加し、同一時刻・逆転期間を拒否するようにした。

### 主な設計判断

- SQL・JSONはwheel内の読み取り専用リソース、SQLiteは利用者が指定する書き込み先として分離した。
- DBパスは`--db`、`WEATHERDB_DB_PATH`、カレントディレクトリ配下の既定値の順に決定する。
- 関係履歴は`UNIQUE (station_id, forecast_area_id, relation_type, valid_from)`で期間を識別し、`valid_to IS NULL`だけを対象とする部分UNIQUE INDEXで現役重複を防ぐ。
- 自動マイグレーションは追加せず、既存Phase 1 DBはバックアップ後に再作成する方針をREADMEへ記載した。

### 追加したテスト

- wheelへのSQL・JSON収録と、ネットワーク無効の隔離prefixへインストールしたCLIのリポジトリ外実行。
- `located_in`の変更、同一マスタ再取込、終了関係の復活、地点消失、`verification_target`非変更。
- 不正ルート、sources欠落、不正日付、非辞書レコード、真偽値の緯度、範囲外経度、不正area_codes、未知コード、CLI終了コードと非Traceback。
- 正常・同一・逆転した予報期間のDB制約。

### 検証結果

- `python -m unittest discover -s tests -v`: 18件、全件成功。
- `python -m compileall -q weatherdb tests`: 成功。
- source CLIの`init → import-areas → status`: 地域45件、観測地点11件、foreign keys有効、WAL。
- wheelには`weatherdb/resources/schema.sql`と`kanagawa.json`を収録。
- 自動テストではリポジトリ外の隔離prefix、追加の手動確認では隔離venvにwheelをインストールし、どちらもconsole scriptの`init → import-areas → status`が成功。
- `PRAGMA integrity_check`: `ok`、`PRAGMA foreign_key_check`: 問題なし。

## 2026-08-28 — PR #2 re-review fixes for commit d9a7148

### 修正内容

- `kanagawa-phase1`の最新`verified_at`確認と更新を同じ即時トランザクションで行い、過去日のマスタを全変更前に拒否するようにした。同日は正規化内容のSHA-256が一致する場合だけ冪等成功とし、不一致はロールバックする。
- 検証済みデータを、JSONキー、`area_code`順の地域、`station_code`順の地点、各地点の`area_codes`順で正規化し、UTF-8のコンパクトJSONからSHA-256を計算して`master_imports.content_sha256`へ保存する。
- 新しいマスタから消えた現役地域は削除せず、`valid_to`と`master_verified_at`を今回の確認日へ更新する。開始日以降へ遡って終了する入力は全体を拒否する。SQL例にも地域・地点の現役条件を追加した。
- `active_to`が確認日以前の地点を廃止済みとして扱い、開始日不明の新規廃止地点はNULLのまま保存する。廃止地点を現役`located_in`候補から除外し、関係を地点と同じ終了日で閉じる。復活時は地点と関係に新しい開始日を設定し、`verification_target`は変更しない。
- 緯度・経度・標高の数値変換で`OverflowError`を`MasterDataError`へ統一し、極端な整数、NaN、無限大、真偽値、範囲外座標をCLIへトレースバックを漏らさず拒否する。

### 設計判断

- 履歴順序判定にはISO 8601の`YYYY-MM-DD`文字列順を使う。排他開始後に最新日を読み、判定、同期、取込記録を1トランザクションで確定する。
- 地域と地点の物理行は参照整合性のため維持し、現役状態は`valid_to IS NULL` / `active_to IS NULL`で検索する。地点テーブルは引き続き1地点1行で、`located_in`は期間行を追加して履歴を保持する。
- `master_imports`のNOT NULL列追加に対する自動マイグレーションは追加せず、既存Phase 1 DBをバックアップ後に再作成する方針を維持する。

### 追加テストと検証結果

- 過去日拒否と完全ロールバック、同日同内容の冪等性、JSON・配列順に依存しないハッシュ、同日競合拒否を追加した。
- 消失区域の終了、現役集計からの除外、予報外部キー維持、再取込、逆転期間のロールバックを追加した。
- 開始日不明の廃止地点、`located_in`の同日終了、`verification_target`維持、地点復活、関係期間逆転のロールバックを追加した。
- 極端な整数、NaN、正負の無限大、真偽値、範囲外座標とCLIの終了コード・非Tracebackを追加した。
- テスト用JSON、SQLite、wheel、インストール先はすべて一時ディレクトリへ作成するようにした。
- `python -m unittest discover -s tests -v`: 既存18件を含む29件、全件成功。
- `python -m compileall -q weatherdb tests`: 成功。
- source CLIとwheel内のSQL・JSONを使うリポジトリ外CLIで、`init → import-areas → status`が成功。
- `PRAGMA integrity_check`: `ok`、`PRAGMA foreign_key_check`: 問題なし。

## 2026-08-28 — PR #2 remaining period-boundary fixes for commit 2737c8c

### 修正内容

- 廃止済み地点が現役として復活する場合、新マスタの`active_from`、未指定なら今回の`verified_at`を開始日候補とし、直前の`active_to`より前ならUPSERT前に`MasterDataError`で拒否するようにした。拒否は取込トランザクション内で行い、全マスタ関連テーブルをロールバックする。
- 正常な復活では決定した同じ開始日を`observation_stations.active_from`と新しい`located_in.valid_from`に使うようにした。
- `validate_master()`で、地域の`valid_from`・`valid_to`と地点の`active_from`・`active_to`の非NULL値が`verified_at`以前であることを検証するようにした。未来日付のエラーには対象コード、項目名、指定日、`verified_at`を含める。

### 期間境界の設計判断

- Phase 1マスタは確認日時点のスナップショットとし、将来予定の開始・終了は登録しない。これにより、インポーターと`sql/queries.sql`の`IS NULL`による現役判定を一致させる。
- 有効期間は半開区間とし、復活開始日と直前の終了日の一致は許可する。開始日が終了日より前になる入力は補正・上書きせずマスタ全体を拒否する。

### 追加テストと検証結果

- 古い開始日の復活拒否と全テーブルの不変、直前終了日と同日・後日の復活成功、地点と`located_in`の開始日一致を追加した。
- 4種の未来日付拒否、`verified_at`と同日の許可、CLIの終了コード1・非Traceback、拒否後のDB不変、最終整合性検査を追加した。
- `python -m unittest discover -s tests -v`: 既存29件と追加13件の合計42件、全件成功。
- `python -m compileall -q weatherdb tests`: 成功。
- source CLIの`init → import-areas → status`: 地域45件、観測地点11件、foreign keys有効、WALで成功。
- wheelを隔離した一時ディレクトリでビルド・インストールし、リポジトリ外のconsole scriptで`init → import-areas → status`が成功。
- `PRAGMA integrity_check`: `ok`、`PRAGMA foreign_key_check`: 空。
## 2026-09-01 — Phase 2 forecast ingestion

### 作業開始時の状態

- `main`、`f186a1f`、`origin/main`と一致し、追跡対象の未コミット変更なし。
- 開始時の既存テストは、実装テスト41件が成功。wheelテスト1件だけはシステムPython 3.14に`pip`がないため失敗した。`python -m compileall -q weatherdb tests`は成功した。
- Phase 1の地域・地点マスタ、履歴同期、既存テストを維持した。

### 実装内容と設計

- 気象庁の神奈川県府県天気予報JSONを`urllib.request`で取得する`fetch-forecast`を追加した。既定URL、timeout、User-Agentを一か所で管理し、timeout、HTTP status、接続、空レスポンスを通常エラーとして扱う。
- JSON decode、短期・週間識別、時刻変換、要素変換、HTTP取得、raw保存、DB保存を分離した。短期・週間は配列位置ではなくtimeSeriesの要素構造で識別する。
- `forecast_runs`へ`document_type`、`raw_file_sha256`、`document_sha256`を追加した。HTTPレスポンス全体のraw hashと、キー順を正規化した文書単位hashを分離した。Phase 1互換用`content_sha256`は残したがPhase 2処理では使用しない。
- `forecasts.forecast_area_id`をnullableにし、nullableな`station_id`外部キーを追加した。CHECKで区域・地点のどちらか一方だけを必須とし、区域・地点別の部分UNIQUE INDEXで自然キー重複を防ぐ。
- 区域天気・降水確率は`forecast_area_id`、短期・週間気温は元JSONの地点コードを`station_id`へ保存する。地点気温を東部・西部へ推測変換しない。
- DB時刻はUTC `+00:00`、日付境界と期間解釈は日本時間。日別天気・気温は日本時間の1日、短期降水確率はtimeDefineから6時間、週間降水確率は日別とした。
- 短期気温は日本時間00:00を最低、09:00を最高としてtimestampから分類し、配列位置を使わない。5時・11時・17時相当fixtureで個数・並びの変化を検証した。安全に解釈できない時刻は構造エラーとする。
- rawレスポンス元bytesを`raw/jma/forecasts/`へ一時ファイル、flush、`fsync`、置換の順で保存する。名前はUTC取得時刻、地域コード、種別、hash断片、nonceを含む。DBには絶対パスとSHA-256だけを保存する。
- 文書識別後に`started` runを作り、全forecast INSERTと`completed`更新を同じtransactionで行う。失敗時はforecastを全rollbackし、別transactionで`failed`と500文字以下の原因を保存する。文書識別前の失敗は発表日時・種別NULLのfailed runにする。
- 同一`document_sha256`はskipし、同一対象日でも発表日時または内容が異なる文書は別runとして履歴追加する。
- 旧Phase 1スキーマは読み取り専用接続で検出し、自動変更・削除せず、バックアップ後の新規作成を案内する方針を採用した。
- `status`へ最後のrunの短いエラー表示を追加した。Phase 3の観測取得、対応付け、降水・気温分析、calibration、Web UIには進んでいない。

### 公式データ確認

- 2026-09-01（日本時間）に`https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json`と`https://www.jma.go.jp/bosai/common/const/area.json`を確認した。
- HTTPレスポンス内で短期文書は2026-09-01 05:00 JST、週間文書は2026-08-31 17:00 JSTと異なる`reportDatetime`だった。
- 短期は東部・西部の天気と6時間降水確率、横浜・小田原の気温、週間は神奈川県の天気・降水確率、横浜の最高・最低気温を確認した。
- 表示用JSONはバージョン固定APIではないため、確認構造と注意点を`docs/data-sources.md`へ記録し、fixtureとraw保存を採用した。

### 追加テスト

- 短期・週間正常parse、文書順入替、5時・11時・17時の気温時刻規則。
- 区域・地点の保存先、UTC変換、日別期間、6時間期間、未知コード、必須キー、配列長、不正日時、範囲外確率、decodeエラー。
- XOR CHECK、区域・地点別重複防止、同一文書skip、異なる発表回の履歴、issued/fetched分離。
- timeout、HTTP status、空レスポンス、raw保存失敗、parser失敗、DB途中失敗rollback、部分forecast非残存。
- CLI成功/失敗コード、非Traceback、DB・マスタ不足、statusエラー、旧Phase 1 DB非変更案内。
- 18件を追加し、既存42件と合わせて60件。

### 実行コマンドと結果

- `python -m unittest discover -s tests -v`: システムPythonでは59件成功、wheelテスト1件のみ`No module named pip`で失敗（開始時と同じ環境要因）。
- `python -m venv --system-site-packages .venv`: リポジトリ内のgitignore対象検証環境を作成。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -q`: 60件、全件成功。
- `.venv\\Scripts\\python.exe -m compileall -q weatherdb tests`: 成功。
- wheel単独テスト: wheel build、offline install、リポジトリ外console scriptの`init → import-areas → status`が成功。
- `python -m weatherdb --db .\\data\\phase2-test.sqlite3 init`: 成功。
- `python -m weatherdb --db .\\data\\phase2-test.sqlite3 import-areas`: 地域45件、観測地点11件、成功。
- `python -m weatherdb --db .\\data\\phase2-test.sqlite3 fetch-forecast`: 実ネットワークで成功。短期・週間2文書、completed 2 run、43 forecastを保存。
- 同じ`fetch-forecast`を再実行: completed 0、forecast 0、2文書skip。DB件数は2 run、43 forecastのまま。
- `python -m weatherdb --db .\\data\\phase2-test.sqlite3 status`: run 2、forecast 43、最後のstatus completed。
- 履歴SQL: `short_term`と`weekly`を別run・別`issued_at`で確認。区域天気11件、区域降水確率21件、地点気温11件。同じtarget_startへ異なる2発表日時が共存した。
- `PRAGMA integrity_check`: `ok`。
- `PRAGMA foreign_key_check`: 0件（問題なし）。

### 現在の制約

- 気象庁表示用JSONの構造変更時はparserとfixture更新が必要。
- システムPythonには`pip`がないため、wheel検証は標準venv内で実施した。実行時機能にはpipも外部依存も不要。
- 観測値取得、予報と実測の対応付け、降水分析、気温誤差、calibration、Web UIはPhase 3以降。

## 2026-09-01 — PR #4 retry・raw整理・INDEX修正

### 原因と修正内容

- `forecast_runs.document_sha256`のUNIQUE INDEXと重複検索がstatusを区別していなかったため、`failed`または異常終了後の`started`が同じ文書の再試行を永久に阻害していた。
- 文書hashのUNIQUE INDEXを`status = 'completed'`だけに適用する部分UNIQUE INDEXへ変更し、重複判定もcompletedだけを対象にした。failed・startedは監査履歴として残したまま、別runで再試行する。
- forecast INSERTとcompleted更新は引き続き同じtransactionで行う。completed更新時にも既存completedを確認し、同時実行で別runが先にcompletedになった場合は競合側をrollbackしてskipする。部分forecastと同一文書の複数completedを残さない。
- raw保存後に全文書がcompleted済みと判定された場合、DBから参照されない今回のrawだけを削除し、`FetchSummary.raw_file_path`をNULLにする。短期・週間の一方が新規・再試行・failedなら共通rawは保持する。
- raw削除に失敗した場合は無言で継続せず、残ったrawを参照するfailed runを監査記録として作成して通常エラーを返す。ファイルが既に存在しない場合は、存在しないパスを記録しない。
- `status`の表示名を「最新保存run取得日時」へ変更し、HTTP試行時刻ではなく最新の保存済みrunの`fetched_at`であることをREADMEへ明記した。
- `forecasts(forecast_run_id)`の通常INDEX `idx_forecasts_run`を追加した。区域・地点別の部分UNIQUE INDEXは維持した。
- READMEのwheel例を`weather_accuracy_db-0.2.0-py3-none-any.whl`へ修正した。

### スキーマ互換性の判断

- 旧Phase 1 DBを自動変更しない方針は維持した。
- PR #4初期版のPhase 2 DBはテーブル変更を伴わないINDEX修正だけで安全に更新できるため、`init`再実行で旧文書INDEXをdrop/recreateし、run INDEXを追加できるようにした。通常の取得処理は必要なINDEXがない場合に`init`再実行を案内する。

### 追加・変更テスト

- parser失敗後の同一JSON再試行、failed履歴保持、成功後の重複skip。
- 残存started runが同一JSONの再試行を阻害しないこと。
- completed競合で片側をrollbackし、completedが1件だけになること。
- 全文書重複時の未参照raw削除、CLIの削除済みパス非表示。
- 短期重複・週間新規の混在時に共通rawを保持すること。
- parser失敗時にfailed runからrawを参照できること。
- raw削除失敗のエラー通知と監査run。
- `PRAGMA index_list/index_info`と`EXPLAIN QUERY PLAN`によるrun INDEX確認、初期Phase 2 INDEXの`init`更新。
- wheelファイル名がバージョン`0.2.0`と一致すること。`.venv`をpackagingテストのリポジトリコピー対象から除外した。
- 既存60件に8件を追加し、合計68件。

### 実行コマンドと結果

- `python -m unittest discover -s tests -v`: 実装テスト67件は成功。システムPython 3.14にpipがないためwheelテスト1件だけ`No module named pip`で失敗した。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`: 68件、全件成功。wheel build、隔離install、リポジトリ外CLIも成功。
- `python -m compileall -q weatherdb tests`: 成功。
- `python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist`: システムPythonにpipがないため未実行相当の失敗。
- `.venv\\Scripts\\python.exe -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist`: 成功。生成物は`weather_accuracy_db-0.2.0-py3-none-any.whl`。
- 新規`data/phase2-retry-test.sqlite3`の`init → import-areas → fetch-forecast → status`: 成功。地域45件、地点11件、completed 2 run、forecast 39件。
- 同じJSONの2回目取得: completed 0、forecast 0、skip 2。runとforecast件数は増えず、rawファイル数も増えなかった。CLIは削除済みrawパスを表示しなかった。
- `PRAGMA integrity_check`: `ok`。
- `PRAGMA foreign_key_check`: 0件。
- completed文書の重複group: 0件。
- `EXPLAIN QUERY PLAN SELECT * FROM forecasts WHERE forecast_run_id = ?`: `idx_forecasts_run`の利用を確認。

### 残っている制約

- 気象庁表示用JSONの構造変更時はparserとfixture更新が必要。
- システムPythonにはpipがないため、wheelを含む完全な検証は既存venvで実施した。実行時の外部Python依存は追加していない。
- HTTP取得試行自体は履歴化せず、保存された文書runだけを履歴化する。全文書重複時の取得時刻はstatusへ反映しない。
- 観測値取得、精度分析、calibration、Web UIはPhase 3以降。
