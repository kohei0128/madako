# Data Profile

dbt metadataと実データのprofiling結果を同じ画面で確認する、ローカルファーストのデータカタログです。

現在は最初のvertical sliceとして、fixtureに保存した1モデルのOverall / Dimension profileをAPIから取得し、Explorerと高密度なColumns一覧に表示します。DATE dimensionのpartition trendとcategorical dimensionのvalue比較にも対応しています。

BigQuery、dbt artifacts、Parquet、DuckDBとの接続はまだ含みません。現在の進捗、実装で具体化した方針、次の開発段階は[開発状況と現在の方針](docs/development-status.md)を参照してください。MVP全体の基準は[MVP要件定義](docs/product-requirements.md)にあります。

## Setup

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv sync

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

fixture JSONをParquetへ置き換え、DuckDB経由で同じAPI契約を返します。完了条件とその後の順序は[次の開発段階](docs/development-status.md#次の開発段階)に記載しています。
