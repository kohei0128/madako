# Data Profile 開発状況と現在の方針

最終更新: 2026-09-02

この文書は、[MVP要件定義](product-requirements.md)を実装する過程で決まった内容と、現在の開発状況を記録する。MVPの目的と対象範囲は要件定義を正とし、この文書では具体化した設計判断と次に行う作業を扱う。

## 現在地

最初のvertical sliceとして、fixtureに保存したprofileをAPI経由でWeb UIへ表示できる状態まで実装した。

現在動作するもの:

- FastAPIによるprofile参照API
- Pydanticによるprofileデータのvalidation
- React / TypeScriptによる1画面のExplorer + Table Detail
- model検索と選択
- Overall profileのColumn一覧
- `All / String / Numeric / Boolean / Date`の型フィルタ
- STRINGのDistinct、NumericのMin / Max、BOOLEANのTRUE率、DATEのMin / Max表示
- NULL Count / NULL Rateと視覚的なバー表示
- `Overall / created_date / service`のProfile By切り替え
- DATE dimensionのNULL Rate heatmap、最新partition、previous partitionとの比較
- DATE dimensionの`Latest 30 / Latest 90 / All`切り替え
- categorical dimensionのvalue間比較
- Parquetへのmodel metadataとcolumn profileの保存
- DuckDBによるOverall / DATE / categorical profileの再構築
- JSON fixtureからParquetを生成する`build-sample`コマンド
- dbt Coreの`manifest.json` / `catalog.json`読み込み
- dbt models / sources / columns / testsのmetadata import
- profile未生成relationのAPI・UI表示
- `tsubo`の10 models＋4 sourcesを使ったartifact integration test
- `stg_zaim_transactions`に限定したBigQuery profiling pilot
- 実行前dry runと`max_bytes_billed`によるcost safety
- Overall＋DATE dimension profileのParquet統合
- dbt `meta.profiling`による対象・dimension・処理量上限の設定
- 設定駆動のSQL生成とBigQuery dry runを行う`plan`コマンド
- plan結果をそのまま実行する設定駆動の複数relation `profile`コマンド
- dimension未指定時のOverall-only profile
- `DataProfile.plan()` / `run()` / `profile()`を中心とする公開Python API
- CLIのplan・profile処理を公開Python APIへ統合
- STRINGの空文字をMissingへ含めるrelation単位の設定とNULL／空文字の内訳保持
- artifact再import時の既存profile引き継ぎ
- APIテストとWebのproduction build

現在のAPIは指定したstorage directoryのParquet filesをDuckDBで読み込む。`fixtures/sample_profiles.json`は開発用profileの入力、`fixtures/tsubo`はdbt artifactsとBigQuery profileから生成したローカルstorageとして使用する。

## 現在のプロダクト方針

プロダクトの主目的は、dbt modelの実データの状態を短時間で理解できるようにすることである。Data Observability、異常検知、AlertingをMVPの主目的にはしない。

DATE dimensionについては、最新partitionや日付ごとの差を表示する。ただし、システムが正常・異常を判定するのではなく、現在存在するデータを時間軸で理解するためのprofilingとして扱う。

そのためMVPでは、以下を行わない。

- anomaly detection
- thresholdによる正常・異常判定
- alert通知
- profile snapshot history
- 過去のprofile実行結果同士の比較

DATE dimensionに並ぶ日付は、1回の最新profile結果に含まれるpartition valuesである。過去に実行したprofile snapshotではない。

## UIで具体化した判断

### Columns一覧を画面の主役にする

右側にColumn Detailを常設せず、Columns一覧へ最大限の表示領域を割り当てる。個別カラムの詳細が必要になった場合は、将来Drawerまたはrow展開として追加する。

一般的なSaaS dashboardより、BigQuery、Databricks、IDE、Database Explorerに近い高密度なUIを目指す。

### OverallとDimensionでテーブル構造を変える

Overallでは次のシンプルな構成を使う。

```text
COLUMN | TYPE | NULL | METRICS
```

Dimension選択中は、dimension values間の違いを理解するための列を追加する。両者のテーブル構造を無理に統一しない。

### 型フィルタを維持する

Allでは異なる型を同じ一覧で俯瞰し、型フィルタ選択中は同じ指標をカラム間で比較しやすい列構成へ切り替える。

```text
All      COLUMN | TYPE | NULL | METRICS
String   COLUMN | NULL | DISTINCT
Numeric  COLUMN | TYPE | NULL | MIN | MAX
Boolean  COLUMN | NULL | TRUE
Date     COLUMN | NULL | MIN DATE | MAX DATE
```

### Profile Byはfilterではなく比較軸

`Overall / created_date / service`は、1つのdimension valueへ絞り込む操作ではない。

- Overall: テーブル全体のprofile
- created_date: 日付ごとのprofileを同時に比較
- service: service valueごとのprofileを同時に比較

UI操作によってBigQuery queryは発行しない。事前にprofileコマンドで計算された結果だけを切り替える。

### DATE dimension

DATE dimensionでは、Columns一覧を次の情報構成にする。

```text
COLUMN | TYPE | NULL RATE BY DATE | LATEST | LATEST METRICS
```

- NULL RATE BY DATE: dimension valuesを時系列順に並べたheatmap
- LATEST: 最新partitionのNULL Rate / NULL Countとprevious partitionの値
- LATEST METRICS: 最新partitionにおける型固有metrics
- Latest 30 / Latest 90 / All: 現在のprofileに含まれる日付valuesの表示範囲

Total rowsはOverall profileのRecord Countを表示する。最新partitionのRecord Countで置き換えず、次のように明確に分離する。

```text
12,480 total rows
Latest partition 2026-08-30 · 528 rows
```

previousとの差は中立的な参考情報として表示し、警告色、anomaly label、異常判定には使わない。

### Categorical dimension

serviceなど、順序を持たず100〜200 valuesになる可能性があるdimensionは、valueごとの大型カードやプルダウンにしない。

- NULL Rateをvalue順のheatmap stripとして表示
- hoverでdimension value、NULL Rate、NULL Countを表示
- 型固有metricsはvalues全体のMin / Maxとして要約

将来、value検索、並び順変更、特定valueの詳細表示が必要になる可能性がある。

## 当初案から変更・具体化した点

### 常設Column Detailパネルを採用しない

一度は「共通指標の一覧 + 選択カラムの右サイドパネル」を実装したが、Columns一覧の表示領域と情報密度を優先して撤回した。

### Dimension valueのプルダウン選択を主操作にしない

当初は`service = business`のように1 valueを選んで表示していた。現在はdimension valuesを同時に比較する方針へ変更した。

### DATE dimensionを単純な横比較表にしない

100〜200 partitionsを想定すると、valueごとに幅の広い列を作る方式は成立しない。NULL Rate heatmapと最新partitionの要約を使う高密度な時系列表示へ変更した。

### Differenceを異常判定として扱わない

最新partitionとprevious partitionの差は有用だが、MVPでは正常・異常を判断しない。差分は`prev.`として補助的に表示する。

## 現在の技術構成

```text
fixtures/sample_profiles.json
        ↓
build-sample
        ↓
models.parquet + column_profiles.parquet
        ↓
DuckDBProfileRepository
        ↓
FastAPI
        ↓
React / TypeScript / Vite
```

Backend:

- Python 3.12+
- FastAPI
- Pydantic
- DuckDB
- Parquet
- uv
- pytest

Frontend:

- React
- TypeScript
- Vite
- chart libraryは使用せず、CSSでbarとheatmapを描画

現在のAPI:

- `GET /api/health`
- `GET /api/models`
- `GET /api/models/{model_name}/profile`

## Profileデータモデルの現在形

API上は、modelに複数の`ProfileSlice`を持たせている。

```text
ModelProfile
└── profiles[]
    ├── dimension_name
    ├── dimension_value
    ├── record_count
    └── columns[]
        ├── name
        ├── data_type
        ├── null_count
        ├── null_rate
        ├── distinct_count
        ├── min_value
        ├── max_value
        └── true_count
```

Overall profileでは`dimension_name`と`dimension_value`をNULLにする。Dimension profileでは、同じ`dimension_name`に対してvalueごとのProfileSliceを持つ。

このAPI契約を維持したまま、保存形式を階層JSONから正規化したParquet relationsへ変更した。物理schemaは[Profile Storage Schema](profile-storage-schema.md)を参照する。

## 完了したStorage slice

fixture JSONをParquetへ変換し、DuckDBから同じAPI contractを返すvertical sliceは完了した。

- model metadataとprofile metricsを別Parquetへ保存
- OverallとDimension profileを共通relationで保存
- DuckDB repositoryからAPIの階層modelを再構築
- Numeric / DATEのMin / Maxをcolumn typeに応じて復元
- Overall、DATE dimension、categorical dimensionのrepositoryテストを追加

## 完了したdbt artifacts slice

`import-dbt`コマンドでdbt Core projectのartifactsをParquetへ取り込める。

```bash
data-profile import-dbt --project-dir <dbt-project> --output-dir <storage-dir>
```

- project packageに属するmodelsとsourcesだけをimport
- `manifest.json`からdescription、materialization、tags、testsを取得
- `catalog.json`から実relationのcolumn orderとcolumn typeを取得
- catalogがない場合はmanifestのcolumn metadataへfallback
- catalogがmanifestより古い場合はwarningを表示
- profileが存在しないrelationは`profiles=[]`と`profiled_at=NULL`で表現

`tsubo`では10 models、4 sources、合計14 relationsをimportできる。現在の`catalog.json`は2026-07-04、`manifest.json`は2026-08-31生成のため、実profiling前にartifactを更新する必要がある。

## 完了したBigQuery pilot

`stg_zaim_transactions`だけを対象に、Overallと`as_of_date`ごとのprofileをBigQueryから生成した。

- Relation: `northern-bliss-362623.tsubo_staging.stg_zaim_transactions`
- 対応columns: 16
- Overall Record Count: 271
- DATE values: 115
- DATE range: 2026-03-01〜2026-07-30
- dry run estimated bytes: 44,762 bytes
- query上限: 1,000,000,000 bytes
- 保存したmetric rows: 1,856
- profile slices: 116（Overall＋115日）

実行結果は`fixtures/tsubo`へ統合され、14 relationsのうち`stg_zaim_transactions`だけがprofile済みである。その他13 relationsはmetadata-onlyのまま維持している。

## 次の開発段階

2026-09-05: `ProfileResult.items`にrelation／dimension単位の実行結果を追加した。query・結果変換失敗時はfail-fastで後続をskipし、storageは更新しない。`succeeded`はquery・変換成功、`storage_updated`は保存完了を表す。保存とplan作成の失敗は例外として通知する。

公開APIに`adapter=`を追加し、`WarehouseAdapter`経由で推定と実queryを実行する。既定の`BigQueryAdapter`は既存bq実装を利用する。SQL生成・結果変換はまだBigQuery固有。次はStorage境界・schema versioning・clean install検証を進める。

dbt設定から対象とdimensionを解決し、実行前にSQLとcostを確認する独立したplanコマンドまで完了した。`stg_zaim_transactions`の実dry runでは44,762 bytesと見積もられ、1 GBの上限内で`READY`になった。

完了済み:

1. dbt `meta.profiling`からenabledとdimensionsを解決できる
2. dbtがmanifestへ解決したproject / directory / model単位の設定継承と上書きを利用できる
3. unsupported column typeをquery対象から安全に除外し理由を表示できる
4. `data-profile plan`は実queryを実行せずSQL、対象、estimated bytesを表示する
5. artifact再import時にも既存profileを`unique_id`で引き継げる

`profile`のplan結果への統合も完了した。`--select`を省略するとenabledな全relation、指定すると一致するrelationだけを対象にする。全項目をdry runしてから、上限内の場合だけ順次実行し、成功後にstorageを一度書き換える。

```text
create_profile_plan（設定・selection・SQL・上限）
    ↓
plan CLI（dry runのみ）
    ↓
profile CLI（READY項目の実行・保存）
```

Parquetはstageへ全fileを生成し、DuckDBで検証してから置換する。置換途中の例外ではbackupから既存fileを復元する。

次は、relationごとの実行結果と失敗理由を構造化する。その後、BigQuery adapter interfaceとpublic Python APIを安定させる。全体の順序とライブラリ化へ進む判定条件は[Roadmap](roadmap.md)を参照する。

## ライブラリ化に向けた境界

`DataProfile`を公開入口として追加し、CLIも同じ`plan()`と`run()`を利用する。`profile()`は両方を一括実行するshortcutである。内部の`create_profile_plan`はCLIに依存せず、推定器とquery runnerを差し替えられるため、artifact取り込み、計画、実行、保存を独立してテストできる。

配布可能なライブラリにする前に、次を整備する。

- `bq` subprocessをBigQuery adapter interfaceの背後へ置く
- plan結果を受け取る共通executorを作り、CLIを薄い呼び出し層にする
- public Python APIの戻り値と例外契約を実利用に基づいて安定させる
- storage / config schemaのversioningとmigration方針を決める
- package metadata、利用者向け設定、BigQuery依存をoptional dependencyとして整理する

これらを行えば、CLI・Webアプリをライブラリの利用者として構成でき、別のdbt projectや将来のwarehouse adapterからも再利用できる。

## 未決事項

- DATE dimension以外の順序付きdimensionをどう宣言するか
- dimension cardinalityの上限と高cardinality時の保存・表示方針
- 複数dimensionを別queryにするか1 queryへ統合するか
- selectorをdbtのselection syntaxへどこまで合わせるか
- process強制終了も含めて複数fileを単一transactionとして切り替える必要があるか
- Column DetailのDrawer / row展開をMVPへ含めるか

## 更新ルール

- プロダクトの目的やMVP範囲を変える場合は`product-requirements.md`を更新する
- 実装で具体化した判断、進捗、次の作業はこの文書を更新する
- 重要な技術選定を変更した場合は、理由と影響を「当初案から変更・具体化した点」へ追記する
