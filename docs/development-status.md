# Madako 開発状況

最終更新: 2026-09-09

プロダクトの目的とMVP範囲は[要件定義](product-requirements.md)、開発順序と完了条件は[Roadmap](roadmap.md)、操作手順は[README](../README.md)、保存契約と復旧手順は[Storage Schema](profile-storage-schema.md)を参照する。この文書は現在の実装と残る制限を扱う。

## 現在地

experimentalな0.1 Python APIとローカルWebアプリを実装済み。

```text
dbt artifacts → DataProfile.plan() → BigQuery dry run
                       ↓
                DataProfile.run()
                       ↓
           Parquet / DuckDB → FastAPI → React
```

| 領域 | 実装済み | 残る制限 |
|---|---|---|
| dbt import | project内のmodels / sources / columns / tests / direct dependencies、catalog優先とmanifest fallback、古いcatalogへの警告。`profile`実行前にも自動import | dbtのparse / buildは呼び出さない |
| 設定 | `madako.toml`の自動検出、project / storage / BigQuery / server設定、CLI override。profilingはenabled、dimensions、max_dimension_values、queryごとのmax_bytes_billed、空文字のMissing算入 | config schema versioningは未対応 |
| Profiling | OverallとDATE / DATETIME / TIMESTAMP / STRING dimension、STRING cardinality guard、型別metrics、NULL bucket | STRING以外のcategorical dimensionは未対応 |
| 型 | STRING / INT64 / FLOAT64 / NUMERIC / BIGNUMERIC / BOOL / DATE / DATETIME / TIMESTAMP、INTEGER / FLOAT / BOOLEANの正規化 | 複合型などは除外 |
| 実行 | 全対象dry run、上限超過時は全skip、fail-fast、構造化した項目別結果、全成功後に1回保存。実BigQuery E2E確認済み | 長時間queryの進捗・timeout・job IDは未対応 |
| Warehouse | 対応型・SQL生成・推定・query実行・結果変換をWarehouseAdapterで差し替え | 既定実装はBigQuery SQLと外部`bq` CLI |
| Storage | ProfileStorage、Parquet schema v1、unique_idによる分離、CSV一括ロード、stage検証、置換失敗の復元 | 同時アクセス・強制終了のtransaction保証なし |
| Web | Explorer、型フィルタ、Overall、DATE比較、categorical比較、Refresh、直接の上流・下流lineage。同梱UIを`madako serve`でAPIと同一portから配信 | 対応ブラウザはCIで検証するChromiumのみ |
| テスト・サンプル | 合成データ、dbt artifact、実BigQuery用Phase 2 E2E、外部最小dbt project smoke。unit / package / browser CIを実行 | 実BigQuery E2Eは費用と認証を伴うため手動実行 |

## 実行と保存の保証

- `plan()`はSQLとdry-run推定を作成する。曖昧な名前のselectionは拒否し、`unique_id`の指定を要求する。
- `run()`は対象relationの存在と、plan作成時のschema・設定を実query前に確認する。変更済みなら再planを要求する。
- query結果では、Overallの存在、slice内のカラム集合・型・重複、件数・率、dimension bucketの行数合計を検証する。
- STRING dimensionはOverallの`distinct_count`を使い、`max_dimension_values`超過時はそのdimensionだけをスキップする。判定専用queryは発行しない。
- `bq`の取得上限100,000 metric rowsに到達した結果は、不完全な可能性があるため保存しない。全件paginationは未実装。
- query・変換失敗は該当項目を`failed`、後続を`skipped`にし、今回の結果を保存しない。
- `succeeded`はqueryと変換の成功。`storage_updated`は保存完了。保存・plan作成の失敗は例外となる。
- 保存時は行単位のINSERTを避けて一時CSVからDuckDBへ一括ロードし、2つのParquetファイルをstageへ生成する。読み戻し検証後に順次置換し、復元にも失敗した場合はbackupを保持して`StorageRecoveryError.recovery_dir`で場所を通知する。
- artifact再import時は、同じ`unique_id`かつschema・profiling設定などの入力が一致する場合にprofileを引き継ぐ。不一致ではprofileと実行日時をクリアする。
- 公開例外は`DataProfileError`を基底にartifact・planning・warehouse・result・storageへ分類する。従来の`ProfilingError`は互換用の基底として維持する。

各queryは独立した実行であり、複数relation／dimension間でWarehouseの同一snapshotを保証しない。書き込みは直列化し、書き込み完了後に読み込む。

## UIの設計判断

- Columns一覧を主役にし、常設のColumn Detailパネルは置かない。
- Profile Byは1つの値へのfilterではなく、dimension valuesの比較軸とする。
- DATE / DATETIME / TIMESTAMPはheatmapと最新・previous partitionを表示する。Latest 30 / 90は保存された時系列bucket数で、暦日数や経過時間ではない。
- Temporal dimensionのNULL bucketは時系列の並び・最新partitionから外し、別の表で表示する。
- STRING dimensionはvalue間のheatmapとmetricsの範囲を表示する。数値が全てNULLなら`—`とする。
- differenceは中立的な参考情報であり、正常・異常判定には使わない。
- Explorerはmetadataだけを取得し、選択relationのprofileを別requestで取得する。識別子は`unique_id`。Refreshは保存済みデータを再取得する。
- `madako profile`はdbt artifact取込、各queryのdry run、実行、保存を逐次表示する。途中失敗時は完了済みの一時結果を破棄し、storageを更新しなかったことを明示する。
- Lineageは詳細画面の末尾に上流・選択relation・下流を横並びで表示し、dbtのdirect dependencyだけを辿る。表示中のrelationへ画面内で移動できる。
- UI操作からBigQuery queryは発行しない。

## 2026-09-05 レビュー後の修正

同名relationへのprofile混入、NULL dimensionの変換失敗、不完全な結果の成功保存、古いplanの成功通知、設定変更後の古いmetrics引き継ぎ、復元失敗時のbackup消失を修正した。回帰テストを追加し、テストがGit管理外の個人用fixtureや`dbt/tsubo/target`に依存する状態を解消した。

repositoryは一覧取得時にrelationごとに接続・queryする方式から、共通接続でまとめて読み取る方式へ変更した。未使用の単発profile適用処理を削除し、CLIのdbt importは公開Python API経由へ統一した。

## 検証

Python unit test、Web production build、`madako` wheel / sdist buildを実行する。作業directory外の一時venvへcore wheelだけをinstallし、FastAPIに依存せずimport・sample生成・読み取りができることを確認している。wheelにWeb UIと`madako` CLIが含まれ、`madako serve`の同一process・portからHTML、API、JS assetを取得できることも確認している。最小dbt projectをrepository外へコピーし、install済みwheelの公開APIによるimport・plan・run・保存も確認している。BigQuery helperを呼ばずに独自型・SQL・結果変換を行うadapterと、0.1形式のadapter互換性もunit testで確認している。

実データ相当（41 models、29,129 metric rows）の保存ベンチマークでは、既存データの読み込み後に行うParquet生成・読み戻し検証が約67.18秒から約0.53秒へ短縮した。保存schemaと読み戻し検証は変更していない。

GitHub ActionsではPython 3.11 / 3.12 / 3.13のunit test、Web build、Playwright、package build、clean install smokeを実行する。Playwrightは`madako serve`が配信する同梱UIに接続し、同名relation選択、Refresh、NULL bucket表示、direct lineageの表示・移動をChromiumで確認する。

## 実環境の確認範囲

2026-09-06に更新済みのdbt artifactを使い、`mart_pl_transactions`、`pl_money_forward`、`stg_zaim_transactions`の3 relationを実BigQueryで同時にprofileした。3項目とも成功し、全成功後のParquet保存、Python APIでの読み戻し、HTTP APIでのmetadata一覧と`unique_id`別profile取得を確認した。

この確認でCLIの`serve`が移行前のrepositoryをserverへ渡してHTTP 500になる不整合を検出し、`ParquetProfileStorage`を渡すよう修正した。

続いて同一project内の一時datasetを使い、NULL partition、空table、2つのDATE dimensionを実query・保存した。上限超過による全skip、plan後のrelation削除によるSucceeded / Failed / Skipped、100,000 metric rows到達時の拒否では、いずれもParquetのhashが変わらず保存されないことを確認した。一時datasetは検証後に削除した。これによりPhase 2の実環境確認は完了した。

NUMERIC / BIGNUMERIC対応後に一時datasetで大きな正負の小数を再検証し、文字列表現のまま精度を失わず保存・読み戻しできることを確認した。tsuboでも再実行し、`mart_pl_transactions.amount`と`pl_money_forward.amount`がskipされず、それぞれNUMERIC / BIGNUMERICとしてprofileされることを確認した。

## 次に決めること

1. STRING dimensionの実BigQuery検証と既定上限の運用評価。
2. 長時間queryの進捗・timeout・job ID。
