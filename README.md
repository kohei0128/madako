# Madako

Madakoは、**dbtのドキュメントと実データの状態を一緒に見られる、ローカルファーストのデータカタログ**です。

「このモデルは何を表すのか」だけでなく、「何行あるか」「NULLは増えていないか」「値はどの範囲か」まで、ブラウザから確認できます。

現在はBigQueryに対応したexperimentalなバージョンです。

## インストール

Python 3.11以上が必要です。CLIとWeb UIを独立した環境へインストールする場合は、[uv](https://docs.astral.sh/uv/)を使用できます。

```bash
uv tool install 'madako[web]'
```

既存のPython環境へインストールする場合は、pipを使用します。

```bash
python -m pip install 'madako[web]'
```

Web UIが不要でPython APIまたはCLIだけを利用する場合は、`madako`をextraなしでインストールできます。BigQueryをprofileするには、Python packageとは別にGoogle Cloud SDKの`bq` CLIをインストールして認証する必要があります。

## Madakoでできること

- dbtのモデル・ソース・カラム・説明・テストを一覧表示
- 実データから行数、NULL率、Distinct数、Min / Maxなどを取得
- DATE / DATETIME / TIMESTAMPや低カーディナリティなSTRINGカラムごとの変化を比較
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

次の例は合成データを使うため、dbt、BigQuery、`bq` CLIは必要ありません。

```bash
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
          dimensions: [event_date, service]
          max_dimension_values: 10000
          treat_empty_string_as_null: true
```

profiling設定を変更した後は、`dbt docs generate`または`dbt parse`でartifactを更新してから、`madako profile`を再実行してください。使い分けは「3. profileして開く」を参照してください。

`dimensions`を省略するとモデル全体だけを集計します。dimensionにはDATE、DATETIME、TIMESTAMPまたはSTRINGを指定できます。STRINGはOverallのDistinct数が`max_dimension_values`（既定10,000）を超える場合、そのdimensionだけをスキップします。Distinct ratioは表示しますが、実行可否には使いません。

`treat_empty_string_as_null: true`を指定すると、STRINGカラムの空文字（`''`）をNULLと合わせてMissingとして集計します。既定値は`false`で、その場合はNULLだけがMissingです。空白だけの文字列（例: `' '`）は空文字に含みません。

事前にクエリ量を確認したいモデルやソースだけ、`max_bytes_billed`を指定します。指定したrelationでは実行前にdry runし、いずれかのクエリが上限を超える場合は実データへのクエリを開始しません。未指定の場合はdry runを省略します。

```yaml
config:
  meta:
    profiling:
      enabled: true
      max_bytes_billed: "1 GB"
```

### 2. Madakoを設定する

dbt projectのルートに`madako.toml`を作ります。

```toml
[project]
dbt_project_dir = "."
storage_dir = ".madako"

[bigquery]
project = "your-gcp-project"
location = "asia-northeast1"
threads = 4

[server]
host = "127.0.0.1"
port = 8000

[storage]
mode = "generations"
keep_generations = 3
```

相対パスは`madako.toml`の場所を基準にします。Madakoは現在のディレクトリから親へ向かって設定ファイルを探すため、project内のどこからでも実行できます。

`bigquery.threads`は同時にprofileするrelation数です（既定4）。異なるrelationは並列に実行し、同じrelation内ではOverallの完了後にdimensionを順番に実行します。一時的に変更する場合は`madako profile --threads 8`のように指定できます。

`storage.mode = "generations"`は、検証済みの新しい世代を作成してから`current` symlinkを切り替えます。既定では現在を含む3世代を保持し、`keep_generations`には2以上を指定できます。単一writerを前提とし、複数processからの同時保存は保証しません。`storage`設定を省略した場合は従来どおりdirect modeです。既存の`DATA_PROFILE_USE_GENERATIONS=1`も互換性のため利用できますが、新しい設定では`madako.toml`を推奨します。

既存のdirect modeのstorageをgenerations modeで開くこともできます。次回の正常な保存から世代directoryへ移行し、それまでは既存の2ファイルを読み取ります。

生成されるstorageをGitで管理しない場合は、`.madako/`を`.gitignore`へ追加してください。

### 3. profileして開く

dbt artifactを生成してからMadakoを実行します。

```bash
dbt docs generate
madako profile
madako serve
```

YAMLの説明、テスト、`meta.profiling`だけを変更した開発中の確認では、より短時間で終わる`dbt parse`を代わりに使用できます。

```bash
dbt parse
madako profile
```

`dbt parse`が更新するのは`manifest.json`だけで、warehouseから取得する`catalog.json`のカラム型や並び順は更新しません。既存の`catalog.json`が古い場合、Madakoは警告を表示したうえでその内容を使用します。`catalog.json`がない場合はYAMLの`data_type`を使用し、どちらにも型がないカラムは`UNKNOWN`としてprofile対象から除外します。モデルやsourceの実カラムを変更した後、初回取り込み時、正確なカラム型を確認したい場合は`dbt docs generate`を実行してください。

`madako profile`は、`manifest.json`と`catalog.json`をstorageへ取り込んでからBigQueryをprofileします。dbtコマンド自体はMadakoから実行しません。

通常時はImport / Plan / Profile / Saveの進捗とsummaryだけを表示します。terminalでは進捗行を更新し、redirectやCIではANSI cursor controlを使わない追記形式になります。dry run、query開始・完了、queryごとの処理量などを確認する場合は`madako profile --verbose`（または`-v`）を使用してください。

terminalのspinnerは処理の完了を待つ間も一定速度で動きます。CIやredirectでは全体の約10%ごとに進捗を追記します。失敗・dimensionの上限超過は通常表示でも確認でき、失敗に伴う未実行queryの詳細は`--verbose`で表示します。summaryのquery数は成功したqueryの数で、スキップ数は別に表示します。`TERM=dumb`では行を書き換えず、色は使用しないため`NO_COLOR`でも表示内容は変わりません。

`max_bytes_billed`は従来どおりbyte数の整数でも指定できます。読みやすい文字列では、`KB` / `MB` / `GB` / `TB`（10進）と`KiB` / `MiB` / `GiB` / `TiB`（2進）を使用できます。

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

- `max_bytes_billed`を指定したrelationだけ、実行前に対象クエリをdry runします。
- dry runしたクエリのいずれかが`max_bytes_billed`を超える場合は、実データへのクエリを開始しません。
- BigQueryが報告した合計処理量をsummaryへ表示します。`--verbose`ではクエリごとの処理量も表示します。
- STRING dimensionはOverallを先に実行し、追加クエリなしでDistinct数を確認してから実行します。
- 異なるrelationのクエリは`bigquery.threads`を上限に並列実行します。同じrelation内のクエリは順番に実行します。
- `max_dimension_values`を超えるSTRING dimensionは理由を表示してスキップし、他のprofileは継続します。
- 途中でクエリや結果検証に失敗した場合、その実行結果はstorageへ保存しません。
- UIの操作だけでBigQueryへのクエリが発行されることはありません。

対応型はSTRING、INT64、FLOAT64、NUMERIC、BIGNUMERIC、BOOL、DATE、DATETIME、TIMESTAMPです。

OverallとDATE / DATETIME / TIMESTAMP / STRING dimensionを生成できます。STRINGのDistinct数とDistinct ratioも表示します。Temporal dimensionのNULL bucketは時系列比較から分離して表示します。各relationの末尾では、dbtの直接依存から上流・下流1階層のlineageを確認できます。UIのRefreshで更新後のstorageを読み直せます。UI操作はBigQuery queryを発行しません。
## 現在の制約

- BigQuery以外のwarehouseにはまだ対応していません。
- dbt selection syntaxには未対応です。`--select`にはモデル名または`unique_id`を指定します。
- STRING以外のcategorical型はdimension queryを生成できません。
- storageはローカルfilesystem向けです。generations modeは更新中のreaderに安定した世代を見せますが、複数writerからの同時保存は保証しません。

Madakoは現在0.1系です。公開APIとstorage形式は、0.xの間に変更される可能性があります。

## Python API

CLIと同じ処理はPythonからも利用できます。

配布packageにはPEP 561の`py.typed` markerを同梱しており、`madako`から公開するAPIのinline型注釈をmypyなどのtype checkerで利用できます。`madako`配下の内部moduleは型情報を含みますが、0.xの間は互換性保証の対象外です。

```python
from madako import DataProfile

catalog = DataProfile.from_dbt_project(
    project_dir="path/to/dbt-project",
    storage_dir=".madako",
)
plan = catalog.plan(select="events")
result = catalog.run(plan)

print(result.successful)
```

Warehouseやstorageは独自実装へ差し替えられます。詳細な契約は[開発状況](https://github.com/kohei0128/madako/blob/main/docs/development-status.md)と[Storage Schema](https://github.com/kohei0128/madako/blob/main/docs/profile-storage-schema.md)を参照してください。

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

`npm run test:e2e`はChromiumと実行に必要な共有ライブラリを自動で準備します。Ubuntuのsystem packageを変更せず、`web/.playwright-deps/`へ展開するため、sudoは不要です。

既定の8000番portが使用中の場合は、`MADAKO_E2E_PORT=18000 npm run test:e2e`のようにE2E用portを変更できます。

## ドキュメント

- [プロダクト要件](https://github.com/kohei0128/madako/blob/main/docs/product-requirements.md)
- [開発状況](https://github.com/kohei0128/madako/blob/main/docs/development-status.md)
- [Roadmap](https://github.com/kohei0128/madako/blob/main/docs/roadmap.md)
- [Storage Schema・復旧手順](https://github.com/kohei0128/madako/blob/main/docs/profile-storage-schema.md)
- [Config Versioning](https://github.com/kohei0128/madako/blob/main/docs/config-versioning.md)
- [PyPI release手順](https://github.com/kohei0128/madako/blob/main/docs/releasing.md)

## License

[MIT License](https://github.com/kohei0128/madako/blob/main/LICENSE)
