# Data Profile

dbt metadataと実データのprofiling結果を同じ画面で確認する、ローカルファーストのデータカタログです。

現在は、Parquetに保存した1モデルのOverall / Dimension profileをDuckDBで読み取り、API経由でExplorerと高密度なColumns一覧に表示します。DATE dimensionのpartition trendとcategorical dimensionのvalue比較にも対応しています。

BigQueryとdbt artifactsにはまだ接続していません。現在の進捗、実装で具体化した方針、次の開発段階は[開発状況と現在の方針](docs/development-status.md)を参照してください。Parquetの定義は[Profile Storage Schema](docs/profile-storage-schema.md)、MVP全体の基準は[MVP要件定義](docs/product-requirements.md)にあります。

## Setup

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv sync
UV_CACHE_DIR=.uv-cache uv run data-profile build-sample

cd web
npm install
```

## Run

ターミナルを2つ使います。

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv run data-profile serve
```

```bash
cd app/data_profile/web
npm run dev
```

ブラウザで `http://localhost:5173` を開きます。API仕様は `http://127.0.0.1:8000/docs` で確認できます。

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

dbt Coreの`manifest.json`と`catalog.json`を読み込み、model / source metadataを現在のcatalogへ統合します。完了条件とその後の順序は[次の開発段階](docs/development-status.md#次の開発段階)に記載しています。
