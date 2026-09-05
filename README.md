# Data Profile

dbt metadataと実データのprofiling結果を同じ画面で確認する、ローカルファーストのデータカタログです。

現在は、dbt artifactsからmodel metadataとprofiling設定を取り込み、BigQueryで生成したOverall / Dimension profileをParquetへ保存し、DuckDB・API経由でExplorerと高密度なColumns一覧に表示します。DATE dimensionのpartition trendとcategorical dimensionのvalue比較にも対応しています。

現在の進捗と実装判断は[開発状況と現在の方針](docs/development-status.md)、ライブラリ化までの順序は[Roadmap](docs/roadmap.md)を参照してください。Parquetの定義は[Profile Storage Schema](docs/profile-storage-schema.md)、MVP全体の基準は[MVP要件定義](docs/product-requirements.md)にあります。

## Setup

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv sync
UV_CACHE_DIR=.uv-cache uv run data-profile build-sample

cd web
npm install
```

## Run

サンプルprofileを表示する場合は、ターミナルを2つ使います。

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv run data-profile serve
```

```bash
cd app/data_profile/web
npm run dev
```

ブラウザで `http://localhost:5173` を開きます。API仕様は `http://127.0.0.1:8000/docs` で確認できます。

### tsuboのdbt metadataを表示する

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv run data-profile import-dbt \
  --project-dir ../../dbt/tsubo \
  --output-dir fixtures/tsubo

UV_CACHE_DIR=.uv-cache uv run data-profile serve \
  --storage-dir fixtures/tsubo
```

artifact再import時には、保存済みのprofile結果をrelationの`unique_id`で引き継ぎます。profile未生成のrelationにはdbt metadata、columns、testsと`Profile not generated`が表示されます。catalogがmanifestより古い場合は、column typesが古い可能性を警告します。

profiling対象はdbt resourceの`meta.profiling`で宣言します。

```yaml
config:
  meta:
    profiling:
      enabled: true
      dimensions:
        - as_of_date
      max_bytes_billed: 1000000000
      treat_empty_string_as_null: true
```

`treat_empty_string_as_null`は既定で`false`です。有効にするとSTRINGの`''`をMissingへ含めます。物理NULLと空文字は別々に保存され、UIでは`MISSING`として合算値を表示し、hoverで内訳を確認できます。空白だけの文字列は対象外です。

実queryを発行せず、設定から対象・SQL・推定処理量・上限判定を確認できます。

```bash
UV_CACHE_DIR=.uv-cache uv run data-profile plan \
  --storage-dir fixtures/tsubo \
  --select stg_zaim_transactions \
  --project northern-bliss-362623 \
  --location asia-northeast1
```

設定済みの全relationをprofilingする場合:

```bash
UV_CACHE_DIR=.uv-cache uv run data-profile profile \
  --storage-dir fixtures/tsubo \
  --project northern-bliss-362623 \
  --location asia-northeast1
```

1 relationだけを実行する場合は`--select stg_zaim_transactions`を追加します。対象、dimensions、処理量上限は各resourceの`meta.profiling`から取得します。`dimensions`が空の場合はOverallだけを生成します。

コマンドは全対象を最初にdry runし、推定処理量がいずれかの`max_bytes_billed`を超える場合は実queryを1件も実行しません。全項目が`READY`の場合だけ順次実行し、すべて成功した後にParquetを一度更新します。

## Python API

保存は`ProfileStorage`（`paths / exists / load / save`）を通して行います。既定の`ParquetProfileStorage`は従来のローカルParquetを使用します。`DataProfile.from_storage(path, storage=...)`と`from_dbt_project(..., storage=...)`で独自の保存処理を指定でき、取込・plan・実行後保存が同じ保存先を使います。指定時はそのstorageを優先し、結果の保存パスもstorageから取得します。現在の契約はローカルの2ファイルを前提としています。

Parquet更新では生成・検証後にファイルを順番に置換し、通常の置換エラー時は復元します。複数ファイルを同時に切り替えるtransactionではないため、同時更新・更新中の読み取り・強制終了時の整合性は保証しません。現段階では書き込みを直列化し、更新完了後に読み込んでください。

実行は`fail-fast`です。queryまたは結果変換に失敗すると後続を停止し、今回の結果は保存しません。

```python
result = app.profile(select="stg_zaim_transactions")
for outcome in result.items:
    print(outcome.item.model.unique_id, outcome.item.dimension,
          outcome.status, outcome.row_count, outcome.error)
print(result.successful, result.storage_updated)
```

`succeeded`はqueryと結果変換の成功を意味します。保存完了は`storage_updated`で判定し、`profiled_models`には保存できたモデルだけが入ります。`row_count`は取得したmetric rows数です。上限超過時は全項目が`skipped`になります。設定・dry-run・保存のエラーは例外として通知されます。CLIとサンプルスクリプトは実行失敗・skip時に終了コード1を返します。

接続処理は`DataProfile.from_storage(path, adapter=...)`または`from_dbt_project(..., adapter=...)`で差し替えられます。既定の`BigQueryAdapter`はインストール済みの`bq`とその認証を使います。独自adapterは`WarehouseAdapter`の`estimate()`と`execute()`を実装します。SQL生成と結果schemaは現在BigQuery向けです。既存の`estimator=`／`runner=`指定は引き続き利用でき、指定された関数がadapterの対応メソッドに優先します。

CLIと同じ処理は、公開Python APIからも実行できます。`from_dbt_project()`は既存の`target/manifest.json`と、存在すれば`catalog.json`をstorageへ取り込みます。`dbt parse`自体は暗黙には実行しません。

```python
from data_profile import DataProfile

app = DataProfile.from_dbt_project(
    project_dir="../../dbt/tsubo",
    storage_dir="fixtures/tsubo",
)

result = app.profile(select="stg_zaim_transactions")
print(result.profiled_models)
```

実query前に内容を確認する場合は、planとrunを分けます。

```python
plan = app.plan(select="stg_zaim_transactions")

for item in plan.items:
    print(item.model.name, item.dimension, item.estimated_bytes, item.executable)

if plan.executable:
    result = app.run(plan)
```

既にimport済みのstorageを利用する場合は、`DataProfile.from_storage("fixtures/tsubo")`を使います。現時点の公開入口は`DataProfile`、`ProfilePlan`、`ProfileResult`、`ProfilingError`です。

### tsubo用サンプルスクリプト

既定では`stg_zaim_transactions`のdry-runだけを行います。

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv run python examples/profile_tsubo.py
```

表示された対象と推定処理量を確認し、実際にprofileを生成・保存する場合は`--execute`を付けます。

```bash
UV_CACHE_DIR=.uv-cache uv run python examples/profile_tsubo.py --execute
```

別の設定済みmodelを指定する場合:

```bash
UV_CACHE_DIR=.uv-cache uv run python examples/profile_tsubo.py \
  --select mart_pl_transactions \
  --execute
```

## Test

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv run pytest

cd web
npm run build
```

## Current API

- `GET /api/health`
- `GET /api/models`
- `GET /api/models/{model_name}/profile`

## Next slice

設定駆動の複数relation実行まで対応しました。次はrelationごとの失敗結果を明確にし、storageのatomic replacementとwarehouse adapter境界を整備します。詳細は[次の開発段階](docs/development-status.md#次の開発段階)に記載しています。
