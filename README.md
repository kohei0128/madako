# Data Profile

dbt metadataと実データのprofiling結果を同じ画面で確認する、ローカルファーストのデータカタログです。

現在は、dbt artifactsからmodel metadataとprofiling設定を取り込み、BigQueryで生成したOverall / Dimension profileをParquetへ保存し、DuckDB・API経由でExplorerと高密度なColumns一覧に表示します。DATE dimensionのpartition trendとcategorical dimensionのvalue比較にも対応しています。

現在の進捗、実装で具体化した方針、次の開発段階は[開発状況と現在の方針](docs/development-status.md)を参照してください。Parquetの定義は[Profile Storage Schema](docs/profile-storage-schema.md)、MVP全体の基準は[MVP要件定義](docs/product-requirements.md)にあります。

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
```

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
