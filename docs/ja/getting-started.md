# Madakoスタートガイド

[English version](../en/getting-started.md) · [ドキュメント一覧](../README.md) ·
[プロジェクトREADME](../../README.md)

Madakoは、dbtのmetadataと実データのprofileを同じブラウザ画面で確認できる、
ローカルファーストのデータカタログです。現在はBigQueryに対応したexperimentalな
0.1系のソフトウェアです。

## 必要環境とインストール

Python 3.11以上が必要です。CLI、Python API、Web UIを独立した環境へ
インストールする場合は、[uv](https://docs.astral.sh/uv/)を使用できます。

```bash
uv tool install madako
```

既存のPython環境へインストールする場合は、pipを使用します。

```bash
python -m pip install madako
```

このpackageだけでPython API、CLI、ビルド済みWeb UIを利用できます。BigQueryを
profileするには、別途Google Cloud SDKの`bq` CLIをインストールして認証する
必要があります。

## sample dataでWeb UIを試す

次の例は同梱された合成データを使うため、dbt、BigQuery、`bq` CLIは必要ありません。

```bash
madako build-sample --output-dir .madako
madako serve --storage-dir .madako
```

ブラウザで <http://127.0.0.1:8000> を開きます。ローカルAPI仕様は
<http://127.0.0.1:8000/docs> です。

## 自分のdbt projectで使う

### 1. profiling対象を選ぶ

対象モデルまたはsourceのYAMLに`meta.profiling`を追加します。

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

profiling設定を変更した後は、`dbt docs generate`または`dbt parse`でartifactを
更新してから、`madako profile`を再実行してください。

`dimensions`を省略するとモデル全体だけを集計します。dimensionにはDATE、
DATETIME、TIMESTAMPまたはSTRINGを指定できます。STRINGはOverallのDistinct数が
`max_dimension_values`（既定10,000）を超える場合、そのdimensionだけをskipします。
Distinct ratioは表示しますが、実行可否には使いません。

`treat_empty_string_as_null: true`を指定すると、STRINGカラムの空文字（`''`）を
NULLと合わせてMissingとして集計します。既定値は`false`で、その場合はNULLだけが
Missingです。空白だけの文字列（例: `' '`）は空文字に含みません。

事前にquery量を確認したいモデルやsourceだけ、`max_bytes_billed`を指定します。
指定したrelationでは実行前にdry runし、いずれかのqueryが上限を超える場合は
実データへのqueryを開始しません。未指定の場合はdry runを省略します。

```yaml
config:
  meta:
    profiling:
      enabled: true
      max_bytes_billed: "1 GB"
```

`max_bytes_billed`はbyte数の整数でも指定できます。読みやすい文字列では、`KB`、
`MB`、`GB`、`TB`（10進）と`KiB`、`MiB`、`GiB`、`TiB`（2進）を使用できます。

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

相対pathは`madako.toml`の場所を基準にします。Madakoは現在のdirectoryから親へ
向かって設定ファイルを探すため、project内のどこからでも実行できます。別の設定
ファイルは`madako --config path/to/madako.toml <command>`で指定します。

`bigquery.threads`は同時にprofileするrelation数です（既定4）。異なるrelationは
並列に実行し、同じrelation内ではOverallの完了後にdimensionを順番に実行します。
一時的に変更する場合は`madako profile --threads 8`のように指定できます。

`storage.mode = "generations"`は、検証済みの新しい世代を作成してから`current`
symlinkをatomicに切り替えます。既定では現在を含む3世代を保持し、
`keep_generations`には2以上を指定できます。ローカルfilesystemと単一writerを
前提とし、複数processからの同時保存は保証しません。`[storage]`を省略した場合は
direct modeです。`DATA_PROFILE_USE_GENERATIONS=1`も互換性のため利用できますが、
新しい設定では`madako.toml`を推奨します。

既存のdirect modeのstorageをgenerations modeで開くこともできます。次回の正常な
保存から世代directoryへ移行し、それまでは既存の2ファイルを読み取ります。

生成されるstorageをGitで管理しない場合は、`.madako/`をdbt projectの
`.gitignore`へ追加してください。

### 3. artifactを生成してprofileを開く

dbt artifactを生成してからMadakoを実行します。

```bash
dbt docs generate
madako profile
madako serve
```

YAMLの説明、テスト、`meta.profiling`だけを変更した開発中の確認では、より短時間で
終わる`dbt parse`を代わりに使用できます。

```bash
dbt parse
madako profile
```

`dbt parse`が更新するのは`manifest.json`だけで、warehouseから取得する
`catalog.json`のカラム型や並び順は更新しません。既存の`catalog.json`が古い場合、
Madakoは警告を表示したうえでその内容を使用します。`catalog.json`がない場合は
YAMLの`data_type`を使用し、どちらにも型がないカラムは`UNKNOWN`としてprofile対象
から除外します。モデルやsourceの実カラムを変更した後、初回取り込み時、正確な
カラム型を確認したい場合は`dbt docs generate`を実行してください。

`madako profile`は、`manifest.json`と`catalog.json`をstorageへ取り込んでから
BigQueryをprofileします。dbtコマンド自体はMadakoから実行しません。

通常時はImport、Plan、Profile、Saveの進捗とsummaryだけを表示します。terminalでは
進捗行を更新し、redirectやCIではANSI cursor controlを使わない追記形式になります。
dry run、query開始・完了、queryごとの処理量などを確認する場合は
`madako profile --verbose`（または`-v`）を使用してください。失敗・dimensionの
上限超過は通常表示でも確認でき、失敗に伴う未実行queryの詳細は`--verbose`で表示
します。summaryのquery数は成功したqueryの数で、skip数は別に表示します。
`TERM=dumb`では行を書き換えず、色は使用しないため`NO_COLOR`でも表示内容は
変わりません。

## 主なコマンド

| コマンド | 用途 |
| --- | --- |
| `madako profile` | dbt artifactを取り込み、対象データをprofileして保存する |
| `madako serve` | 保存済みのcatalogをローカルWeb UIで開く |
| `madako import-dbt` | BigQueryへ接続せず、dbt metadataだけを更新する |
| `madako plan --show-sql` | 保存済みmetadataから見積もりとSQLを確認する |
| `madako build-sample` | 動作確認用の合成storageを作る |

各commandのoptionで設定を一時的に上書きできます。

```bash
madako --help
madako profile --help
```

## queryの安全性

- `max_bytes_billed`を指定したrelationだけ、実行前にqueryをdry runします。
- dry runしたqueryのいずれかが上限を超える場合、そのrelationでは実queryを開始しません。
- BigQueryが報告した合計処理量をsummaryへ表示し、verboseではqueryごとの処理量も表示します。
- STRING dimensionはOverallを先に実行し、追加queryなしでDistinct数を確認してから実行します。
- 異なるrelationのqueryは`bigquery.threads`を上限に並列実行し、同じrelation内では順番に実行します。
- `max_dimension_values`を超えるSTRING dimensionは理由を表示してskipし、他のprofileは継続します。
- queryまたは結果検証に失敗した場合、その実行結果はstorageへ保存しません。
- Web UIの閲覧や操作だけでBigQuery queryが発行されることはありません。

## 対応profileと現在の制約

対応型はSTRING、INT64、FLOAT64、NUMERIC、BIGNUMERIC、BOOL、DATE、DATETIME、
TIMESTAMPです。

OverallとDATE、DATETIME、TIMESTAMP、STRING dimensionを生成できます。STRINGの
Distinct数とDistinct ratioも表示します。Temporal dimensionのNULL bucketは
時系列比較から分離して表示します。各relationの末尾では、dbtの直接依存から上流・
下流1階層のlineageを確認できます。Refreshは保存済みstorageを読み直すだけで、
BigQuery queryを発行しません。

現在の制約:

- BigQuery以外のwarehouseにはまだ対応していません。
- dbt selection syntaxには未対応です。`--select`にはモデル名または`unique_id`を指定します。
- STRING以外のcategorical型はdimension queryを生成できません。
- storageはローカルfilesystem向けです。generations modeは更新中のreaderに安定した
  世代を見せますが、複数writerからの同時保存は保証しません。

Madakoは現在0.1系です。公開APIとstorage形式は、0.xの間に変更される可能性があります。

## Python API

CLIと同じ処理はPythonからも利用できます。

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

配布packageにはPEP 561の`py.typed` markerを同梱しており、mypyなどのtype checkerが
Madakoの公開APIのinline型注釈を利用できます。`madako`配下の内部moduleにも型情報は
ありますが、0.xの間は互換性保証の対象外です。

Warehouseやstorageは独自実装へ差し替えられます。現在のcontractは
[開発状況](../development-status.md)と[Storage Schema](../profile-storage-schema.md)を
参照してください。

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

`npm run test:e2e`はChromiumと必要な共有libraryをproject内の
`web/.playwright-deps/`へ準備するため、Ubuntuのsystem packageを変更せずsudoも
不要です。既定の8000番portが使用中の場合は、
`MADAKO_E2E_PORT=18000 npm run test:e2e`のように変更できます。

技術contractとrelease手順は[ドキュメント一覧](../README.md)を参照してください。
