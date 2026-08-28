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
