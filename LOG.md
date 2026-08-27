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
