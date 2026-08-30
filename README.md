# Data Profile

dbt metadataと実データのprofiling結果を同じ画面で確認する、ローカルファーストのデータカタログです。

現在は最小の縦切りとして、fixtureに保存した1モデルのprofileをAPIから取得し、ExplorerとTable Detailに表示します。BigQuery、dbt artifacts、Parquet、DuckDBとの接続はまだ含みません。

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

fixture JSONをParquetへ置き換え、DuckDB経由で同じAPI契約を返します。その後、dbt artifactsのmetadataを統合し、最後にBigQueryでprofileを生成するCLIを追加します。

