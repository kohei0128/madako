# Madako

Madakoは、**dbtのドキュメントと実データの状態を一緒に見られる、ローカルファーストのデータカタログ**です。

「このモデルは何を表すのか」だけでなく、「何行あるか」「NULLは増えていないか」「値はどの範囲か」まで、ブラウザから確認できます。

現在はBigQueryに対応したexperimentalなバージョンです。

## Madakoでできること

- dbtのモデル・ソース・カラム・説明・テストを一覧表示
- 実データから行数、NULL率、Distinct数、Min / Maxなどを取得
- DATEカラムごとの変化を比較
- profiling対象とクエリごとの上限をdbtのYAMLで管理
- 結果をローカルのParquetへ保存し、`madako serve`だけで閲覧

```text
dbt artifacts + BigQuery
          ↓
        Madako
          ↓
  Parquet storage → Web UI
```

## まず画面を試す

Python 3.12以上と[uv](https://docs.astral.sh/uv/)が必要です。次の例は合成データを使うため、dbtやBigQueryへの接続は必要ありません。

```bash
uv tool install 'madako[web] @ git+https://github.com/kohei0128/madako.git'
madako build-sample --output-dir .madako
madako serve --storage-dir .madako
```

ブラウザで <http://127.0.0.1:8000> を開きます。API仕様は <http://127.0.0.1:8000/docs> です。

## 自分のdbt projectで使う

### 1. profiling対象を選ぶ

対象モデルのYAMLに`meta.profiling`を追加します。

```yaml
models:
  - name: events
    config:
      meta:
        profiling:
          enabled: true
          dimensions: [event_date]
          max_bytes_billed: 1000000000
```

`dimensions`を省略するとモデル全体だけを集計します。現在、実データから生成できるdimensionはDATE型です。

### 2. Madakoを設定する

dbt projectのルートに`madako.toml`を作ります。

```toml
[project]
dbt_project_dir = "."
storage_dir = ".madako"

[bigquery]
project = "your-gcp-project"
location = "asia-northeast1"

[server]
host = "127.0.0.1"
port = 8000
```

相対パスは`madako.toml`の場所を基準にします。Madakoは現在のディレクトリから親へ向かって設定ファイルを探すため、project内のどこからでも実行できます。

生成されるstorageをGitで管理しない場合は、`.madako/`を`.gitignore`へ追加してください。

### 3. profileして開く

dbt artifactを生成してからMadakoを実行します。

```bash
dbt docs generate
madako profile
madako serve
```

`madako profile`は、`manifest.json`と`catalog.json`をstorageへ取り込んでからBigQueryをprofileします。dbtコマンド自体はMadakoから実行しません。

BigQueryの処理には、インストール・認証済みの`bq` CLIが必要です。

## 主なコマンド

| コマンド | 用途 |
| --- | --- |
| `madako profile` | dbt artifactを取り込み、対象データをprofileする |
| `madako serve` | 保存済みのカタログをブラウザで開く |
| `madako import-dbt` | BigQueryへ接続せず、dbt metadataだけを更新する |
| `madako plan --show-sql` | 保存済みmetadataから見積もりとSQLを確認する |
| `madako build-sample` | 動作確認用の合成storageを作る |

一時的に設定を変える場合は各コマンドのオプションを利用できます。別の設定ファイルは`madako --config path/to/madako.toml <command>`で指定します。

```bash
madako --help
madako profile --help
```

## 安全性

- 実行前に対象クエリをすべてdry runします。
- `max_bytes_billed`を超えるクエリがあれば、実データへのクエリを開始しません。
- 途中でクエリや結果検証に失敗した場合、その実行結果はstorageへ保存しません。
- UIの操作だけでBigQueryへのクエリが発行されることはありません。

対応型はSTRING、INT64、FLOAT64、NUMERIC、BIGNUMERIC、BOOL、DATEです。

OverallとDATE dimensionを生成できます。categorical dimensionは保存済みデータの表示のみ対応しています。DATEのNULL bucketは日付比較から分離して表示します。各relationの末尾では、dbtの直接依存から上流・下流1階層のlineageを確認できます。UIのRefreshで更新後のstorageを読み直せます。UI操作はBigQuery queryを発行しません。
## 現在の制約

- BigQuery以外のwarehouseにはまだ対応していません。
- dbt selection syntaxには未対応です。`--select`にはモデル名または`unique_id`を指定します。
- categorical dimensionは表示のみ対応し、profiling queryはまだ生成できません。
- storageはローカル利用向けです。同時更新や更新中の読み取りは保証していません。

Madakoは現在0.1系です。公開APIとstorage形式は、0.xの間に変更される可能性があります。

## Python API

CLIと同じ処理はPythonからも利用できます。

```python
from data_profile import DataProfile

catalog = DataProfile.from_dbt_project(
    project_dir="path/to/dbt-project",
    storage_dir=".madako",
)
plan = catalog.plan(select="events")
result = catalog.run(plan)

print(result.successful)
```

Warehouseやstorageは独自実装へ差し替えられます。詳細な契約は[開発状況](docs/development-status.md)と[Storage Schema](docs/profile-storage-schema.md)を参照してください。

## 開発

```bash
git clone https://github.com/kohei0128/madako.git
cd madako
uv sync
uv run pytest
uv build
```

Web UIを変更するときだけNode.jsが必要です。

```bash
cd web
npm ci
npm run build
npm run test:e2e
```

## ドキュメント

- [プロダクト要件](docs/product-requirements.md)
- [開発状況](docs/development-status.md)
- [Roadmap](docs/roadmap.md)
- [Storage Schema・復旧手順](docs/profile-storage-schema.md)
- [Config Versioning](docs/config-versioning.md)

## License

[MIT License](LICENSE)
