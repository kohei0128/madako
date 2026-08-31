# Data Profile 開発状況と現在の方針

最終更新: 2026-08-31

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
- APIテストとWebのproduction build

現在のデータは`fixtures/sample_profiles.json`から読み込んでいる。BigQuery、dbt artifacts、Parquet、DuckDBにはまだ接続していない。

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
JsonProfileRepository
        ↓
FastAPI
        ↓
React / TypeScript / Vite
```

Backend:

- Python 3.12+
- FastAPI
- Pydantic
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

このAPI契約は次のParquet / DuckDB実装でも維持する。保存時のParquet schemaは、現在の階層JSONをそのまま保存するのではなく、要件定義にある共通profile relationへ正規化する予定である。

## 次の開発段階

次は、fixture JSONをParquetへ置き換え、DuckDBから現在と同じAPIレスポンスを組み立てるvertical sliceを実装する。

完了条件:

1. profile relationのParquet schemaを確定する
2. サンプルprofileをParquetとして生成できる
3. DuckDBでOverallとDimension profileを取得できる
4. `JsonProfileRepository`をDuckDB実装へ差し替えられる
5. 現在のAPI contractを変更せずWeb UIを表示できる
6. Overall、DATE dimension、categorical dimensionのrepositoryテストを追加する

この段階では、まだBigQuery queryを実行しない。保存・読み取り境界を先に安定させた後、次の順序で進める。

```text
Parquet + DuckDB
    ↓
dbt artifactsの読み込み
    ↓
profiling対象とdimension設定の解決
    ↓
BigQuery SQL生成とcost safety
    ↓
profile CLIによる実行・保存
```

## 未決事項

- Parquetの物理schemaとfile layout
- model metadataとprofile metricsを同じParquetへ保存するか分離するか
- DATE dimension以外の順序付きdimensionをどう宣言するか
- dimension cardinalityの上限と高cardinality時の保存・表示方針
- profile対象を指定するdbt `meta` schema
- BigQuery dry runと`max_bytes_billed`の具体的な設定方法
- 最新profileを安全に置き換えるfile operation
- Column DetailのDrawer / row展開をMVPへ含めるか

## 更新ルール

- プロダクトの目的やMVP範囲を変える場合は`product-requirements.md`を更新する
- 実装で具体化した判断、進捗、次の作業はこの文書を更新する
- 重要な技術選定を変更した場合は、理由と影響を「当初案から変更・具体化した点」へ追記する
