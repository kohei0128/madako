# Madako 開発状況

最終更新: 2026-09-14

Madako 0.1系は、experimentalなPython API、CLI、ローカルWebアプリを提供する。操作方法は[日本語スタートガイド](ja/getting-started.md)、保存形式と復旧方法は[Profile Storage Schema](profile-storage-schema.md)を参照する。この文書は現行実装と既知の制約だけを扱い、完了済みの計画や変更履歴はGitに残す。

## 現在の構成

```text
dbt artifacts → plan → optional BigQuery dry run → profile queries
                                                    ↓
React ← FastAPI ← DuckDB ← local Parquet storage ← save
```

| 領域 | 現在の実装 | 既知の制約 |
| --- | --- | --- |
| dbt | models、sources、columns、tests、直接依存を`manifest.json`と`catalog.json`から取り込む | dbtコマンド自体は実行しない。dbt selection syntaxには未対応 |
| Warehouse | `WarehouseAdapter`で対応型、SQL生成、dry run、実行、結果変換を差し替え可能 | 同梱adapterはBigQuery向けで、外部`bq` CLIを使用 |
| Profile | OverallとDATE / DATETIME / TIMESTAMP / STRING dimension、型別metric、NULL bucket、STRING cardinality guard | STRING以外のcategorical dimension、複合型、履歴比較や異常判定は未対応 |
| 実行 | relation単位の並列実行、任意の`max_bytes_billed`、stale plan検出、fail-fast、全成功後の一括保存 | queryの途中進捗、timeout、job ID表示、結果paginationは未対応 |
| Storage | `ProfileStorage`、Parquet schema v1、direct / generations mode、stage検証と復旧 | ローカルfilesystem・単一writer向け。強制終了をまたぐtransaction保証はない |
| Web | metadataとprofileの閲覧、型filter、dimension比較、Refresh、直接の上流・下流lineage | UIからprofile実行はできない。ブラウザE2EはChromiumのみ |

## 重要な実行保証

- `plan()`は`max_bytes_billed`を設定したrelationだけdry runする。1つでも上限を超えるplanは、含まれる実queryをすべてskipする。
- `run()`はrelation、schema、profiling設定、計算versionがplan作成時から変わっていないことを実行前に確認する。
- Overall、column集合・型・重複、件数・率、dimension bucketの行数合計を検証する。`bq`の取得上限100,000 metric rowsに達した不完全な可能性がある結果は拒否する。
- STRING dimensionはOverallの`distinct_count`を使い、`max_dimension_values`超過時はそのdimensionだけskipする。判定専用queryは発行しない。
- queryまたは結果変換に失敗すると、後続項目をskipし、そのrunのprofile結果を保存しない。実行前に取り込んだdbt metadataは別途保存済みである。
- 各queryは独立しており、複数relationやdimensionを同じWarehouse snapshotで読む保証はない。
- artifact再import時は、同じ`unique_id`で計算結果に影響する入力と`PROFILE_COMPUTATION_VERSION`が一致するprofileだけを引き継ぐ。

保存時のatomicity、互換性、復旧手順は[Profile Storage Schema](profile-storage-schema.md)、profile無効化の規則は[Config and Storage Versioning](config-versioning.md)を正とする。

## 自動検証

GitHub Actionsでは次を実行する。

- Python 3.11 / 3.12 / 3.13 / 3.14のunit test
- Web production buildとChromium Playwright E2E
- wheel / sdist build、配布内容とmetadataの検証
- clean environmentでのCLI、Python API、同梱Web UIのsmoke test
- repository外へコピーした最小dbt projectでのimport・plan・run・保存

実BigQuery E2Eは費用と認証を伴うため自動化していない。2026-09-06に複数relation、NULL partition、空table、複数dimension、上限超過、途中失敗、取得上限、NUMERIC / BIGNUMERICの精度保持を手動確認した。再確認には[`examples/verify_phase2_bigquery.py`](../examples/verify_phase2_bigquery.py)を使用する。

## 次の検討事項

- STRING dimensionの実データでの上限評価
- 長時間queryの進捗、timeout、job ID
- dbt selection syntax、結果pagination、共有storage、追加Warehouse adapter
