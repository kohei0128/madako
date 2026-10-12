# Madakoスタートガイド

[English version](../en/getting-started.md) · [ドキュメント一覧](../README.md) · [プロジェクトREADME](../../README.md)

Madakoは、dbtのmetadataと実データのprofileを同じブラウザ画面で確認できる、ローカルファーストのデータカタログです。現在はBigQueryに対応したexperimentalな0.1系のソフトウェアです。

## 必要環境とインストール

Python 3.11以上が必要です。CLI、Python API、Web UIを独立した環境へインストールする場合は、[uv](https://docs.astral.sh/uv/)を使用できます。

```bash
uv tool install madako
```

既存のPython環境へインストールする場合は、pipを使用します。

```bash
python -m pip install madako
```

このpackageだけでPython API、CLI、ビルド済みWeb UIを利用できます。BigQueryをprofileするには、別途Google Cloud SDKの`bq` CLIをインストールして認証する必要があります。

## sample dataでWeb UIを試す

次の例は同梱された合成データを使うため、dbt、BigQuery、`bq` CLIは必要ありません。

```bash
madako demo
```

ブラウザで <http://127.0.0.1:8000> を開きます。demo用のsample storageは一時的に作成され、serverを停止すると削除されます。ローカルAPI仕様は<http://127.0.0.1:8000/docs> です。

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

profiling設定を変更した後は、`dbt docs generate`または`dbt parse`でartifactを更新してから、`madako profile`を再実行してください。

`dimensions`を省略するとモデル全体だけを集計します。dimensionにはDATE、DATETIME、TIMESTAMPまたはSTRINGを指定できます。STRINGはOverallのDistinct数が`max_dimension_values`（既定10,000）を超える場合、そのdimensionだけをskipします。Distinct ratioは表示しますが、実行可否には使いません。

`treat_empty_string_as_null: true`を指定すると、STRINGカラムの空文字（`''`）をNULLと合わせてMissingとして集計します。既定値は`false`で、その場合はNULLだけがMissingです。空白だけの文字列（例: `' '`）は空文字に含みません。

`max_bytes_billed`は、1回のプロファイリングでそのmodelまたはsource全体に使える課金bytesの上限です。Overallと設定した全dimensionのqueryをdry runし、見積の**合計**を上限と比較します。1つでもrelationの上限を超える場合、そのplanに含まれるすべての実queryを開始しません。未指定のrelationではdry runを省略します。

たとえば上限が`"1 GB"`の場合、各600 MBのqueryが2本あれば合計1.2 GBとなり、実行できません。`madako plan`にはqueryごとの見積、relationの合計見積、上限を表示します。

relation内の全queryで上限を共有します。たとえば上限が1 GBで、最初のqueryが600 MBを使った場合、後続queryで使えるのは残り400 MBです。見積と実際の使用量には差があるため、事前の見積が上限内でも、実行中に予算が足りなくなれば処理を停止します。

```yaml
config:
  meta:
    profiling:
      enabled: true
      max_bytes_billed: "1 GB"
```

`max_bytes_billed`はbyte数の整数でも指定できます。読みやすい文字列では、`KB`、`MB`、`GB`、`TB`（10進）と`KiB`、`MiB`、`GiB`、`TiB`（2進）を使用できます。

対象行を絞る場合は、modelまたはsourceに`where`を指定します。次の例では、`event_date`を使って7日前以降の行を選択します。

```yaml
config:
  meta:
    profiling:
      enabled: true
      where: "event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)"
      dimensions: [event_date, service]
      max_bytes_billed: "5 GiB"
```

`where`には`WHERE`キーワードを含まないBigQuery SQLの条件式を記述します。Madakoは前後の空白を除去し、空の条件式を設定エラーにします。SQLの構文や列名はdry runまたは実行時にBigQueryが検証します。未指定または`null`なら全行を対象にします。Overallと全dimensionの集計前に同じ条件を適用し、各metricは選択した行に対する実測値になります。queryごとに同じ行を選ぶ条件を使ってください。`RAND()`のような非決定的な式では、dimensionの行数合計がOverallと一致せず検証エラーになる場合があります。

対象行を絞っても、処理量が同じ割合で減るとは限りません。`max_bytes_billed`を設定すると、条件を適用したqueryで使用量を見積もります。`madako plan`で条件と見積を確認し、`--show-sql`で生成queryを表示できます。

Web UIでは`Filtered`と条件式を表示し、行数を`profiled rows`と表記します。Overallは条件に合う全行を意味します。条件の追加・変更・削除後にdbt artifactを再importすると保存済みprofileが無効になり、再profileで結果が更新されます。`CURRENT_DATE()`を使う条件はquery実行時に評価され、保存済み結果は表示されたprofiling日時のものです。サンプリングには対応しません。

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

相対pathは`madako.toml`の場所を基準にします。Madakoは現在のdirectoryから親へ向かって設定ファイルを探すため、project内のどこからでも実行できます。別の設定ファイルは`madako --config path/to/madako.toml <command>`で指定します。

`bigquery.threads`は同時にprofileするrelation数です（既定4）。異なるrelationは並列に実行し、同じrelation内ではOverallの完了後にdimensionを順番に実行します。一時的に変更する場合は`madako profile --threads 8`のように指定できます。

`storage.mode = "generations"`は、検証済みの新しい世代を作成してから`current` symlinkをatomicに切り替えます。既定では現在を含む3世代を保持し、`keep_generations`には2以上を指定できます。ローカルfilesystemと単一writerを前提とし、複数processからの同時保存は保証しません。`[storage]`を省略した場合はdirect modeです。`DATA_PROFILE_USE_GENERATIONS=1`も互換性のため利用できますが、新しい設定では`madako.toml`を推奨します。

既存のdirect modeのstorageをgenerations modeで開くこともできます。次回の正常な保存から世代directoryへ移行し、それまでは既存の2ファイルを読み取ります。

生成されるstorageをGitで管理しない場合は、`.madako/`をdbt projectの`.gitignore`へ追加してください。

### 3. artifactを生成してprofileを開く

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

通常時はImport、Plan、Profile、Saveの進捗とsummaryだけを表示します。terminalでは進捗行を更新し、redirectやCIではANSI cursor controlを使わない追記形式になります。dry run、query開始・完了、queryごとの処理量などを確認する場合は`madako profile --verbose`（または`-v`）を使用してください。失敗・dimensionの上限超過は通常表示でも確認でき、失敗に伴う未実行queryの詳細は`--verbose`で表示します。summaryのquery数は成功したqueryの数で、skip数は別に表示します。`TERM=dumb`では行を書き換えず、色は使用しないため`NO_COLOR`でも表示内容は変わりません。

## 主なコマンド

| コマンド | 用途 |
| --- | --- |
| `madako demo` | 一時的なsample dataを使ってローカルWeb UIを起動する |
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
- relationごとのdry run見積の合計が上限を超える場合、plan全体の実queryを開始しません。実行時もrelation内の全queryで課金bytesの残額を共有します。
- 実際の合計処理量をsummaryへ表示し、verboseではqueryごとの処理量も表示します。
- STRING dimensionはOverallを先に実行し、追加queryなしでDistinct数を確認してから実行します。
- 異なるrelationのqueryは`bigquery.threads`を上限に並列実行し、同じrelation内では順番に実行します。
- `max_dimension_values`を超えるSTRING dimensionは理由を表示してskipし、他のprofileは継続します。
- queryまたは結果検証に失敗した場合、その実行結果はstorageへ保存しません。
- Web UIの閲覧や操作だけでBigQuery queryが発行されることはありません。

## 対応profileと現在の制約

対応型はSTRING、INT64、FLOAT64、NUMERIC、BIGNUMERIC、BOOL、DATE、DATETIME、TIMESTAMPです。

OverallとDATE、DATETIME、TIMESTAMP、STRING dimensionを生成できます。STRINGのDistinct数とDistinct ratioも表示します。Temporal dimensionのNULL bucketは時系列比較から分離して表示します。各relationの末尾では、dbtの直接依存から上流・下流1階層のlineageを確認できます。Refreshは保存済みstorageを読み直すだけで、BigQuery queryを発行しません。

現在の制約:

- BigQuery以外のwarehouseにはまだ対応していません。
- dbt selection syntaxには未対応です。`--select`にはモデル名または`unique_id`を指定します。
- STRING以外のcategorical型はdimension queryを生成できません。
- storageはローカルfilesystem向けです。generations modeは更新中のreaderに安定した世代を見せますが、複数writerからの同時保存は保証しません。

Madakoは現在0.1系です。公開APIとstorage形式は、0.xの間に変更される可能性があります。

## FAQ: Madakoとdbt-profilerの違いは何ですか？

[dbt-profiler](https://github.com/data-mie/dbt-profiler)は、profiling用SQLやメモリ上のtableを生成し、Markdown table、schema YAML、docs blockを出力できるmacroを提供するdbt packageです。print系macroを使うだけならdbt modelは増えません。profile結果をdbt modelとして永続化する使い方では、そのmodelがdbt DAGのnodeに加わります。

Madakoは異なるworkflowを扱います。設定したdbt modelやsourceを対象にBigQueryでprofileを実行し、結果をローカルParquetへ分けて保存し、dbt metadataとともにWeb UIへ表示します。profiling用dbt modelや永続的なBigQuery tableは追加せず、保存済み結果をWeb UIで開く際にBigQueryへ再queryすることもありません。

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

配布packageにはPEP 561の`py.typed` markerを同梱しており、mypyなどのtype checkerがMadakoの公開APIのinline型注釈を利用できます。`madako`配下の内部moduleにも型情報はありますが、0.xの間は互換性保証の対象外です。

Warehouseやstorageは独自実装へ差し替えられます。現在のcontractは[開発状況](../development-status.md)と[Storage Schema](../profile-storage-schema.md)を参照してください。

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

`npm run test:e2e`はChromiumと必要な共有libraryをproject内の`web/.playwright-deps/`へ準備するため、Ubuntuのsystem packageを変更せずsudoも不要です。既定の8000番portが使用中の場合は、`MADAKO_E2E_PORT=18000 npm run test:e2e`のように変更できます。

技術contractとrelease手順は[ドキュメント一覧](../README.md)を参照してください。
