# dbt Core向け 実データ統合型データカタログ

## MVP要件定義

> この文書はMVPのプロダクト要件を定義する。未実装の要件も含む。実装の進捗、要件を具体化する過程で決まったUI方針、当初案からの変更点は[development-status.md](development-status.md)を参照する。

### 1. プロダクトの目的

dbtモデルの実データの状態を、SQLを書かず、BIツールにも接続せず、カタログを見るだけで素早く理解できるようにする。

特に、

* このカラムはどれくらいNULLなのか
* STRINGなら値の種類はどれくらいあるのか
* 数値ならどの範囲の値が入っているのか
* BOOLEANならTRUEがどれくらいあるのか
* DATEならどの期間の値が入っているのか
* 指定したdimensionごとにデータの状態がどう違うのか

を一目で確認できることを重視する。

成功イメージは、

> 初めて見るdbtモデルでも、テーブル詳細画面を30秒程度見れば、実データの大まかな状態を把握できる。

---

## 2. 対象ユーザー

### Primary

* Data Engineer
* Analytics Engineer

### Secondary

* SQLを利用するData Analyst

非技術者向けBusiness CatalogはMVPでは対象外とする。

---

## 3. 初期対応範囲

### dbt

dbt Coreのみ対応する。

主な対象：

* dbt models
* dbt sources

以下はMVPでは必須としない。

* dbt Cloud専用機能
* seed
* snapshot
* ephemeral model

### Warehouse

MVPではBigQueryのみ対応する。

他Warehouse対応はMVP完成後に検討する。

---

# 4. 基本UX

BigQueryの管理画面に近い探索体験を想定する。

画面は基本的に1画面構成とする。

### 左ペイン

Explorerとして、

* Project / Database
* Schema / Dataset
* Table / Model

を階層表示する。

テーブル / モデル検索も可能にする。

### 右ペイン

左側で選択したテーブルの詳細を表示する。

ページ遷移を多用せず、

> Explorer + Table Detail

のシンプルな構成を基本とする。

---

# 5. テーブル詳細画面

dbt metadataと実データのprofiling結果を同じ画面で表示する。

### Metadata

MVP候補：

* model / table name
* database
* schema / dataset
* description
* materialization
* tags
* dbt tests
* record count
* profiling実行日時

### Column一覧

各columnについて、

* column name
* data type
* description
* profiling metrics

を一覧で表示する。

### Lineage

詳細画面の末尾に、選択中のrelationを中心として直接の上流・下流を横並びで表示する。全体グラフではなく1階層だけに限定し、relationを選ぶとその詳細へ移動できる。

---

# 6. UIの最重要要件

単なる数値一覧ではなく、

> どのcolumnがどういう状態なのか、一目で理解できること。

例えばNULL率について、

* 数値
* セル内バー
* 濃淡

などを使い、

> このcolumnはほとんどNULL

ということが一覧を見るだけで分かるようにする。

UIの主目的は、

> 「情報を表示する」ことではなく「データを理解する時間を短くする」こと。

---

# 7. MVP対応データ型

MVPでは以下のみ対応する。

* STRING
* INTEGER / INT64
* FLOAT / FLOAT64
* NUMERIC
* BIGNUMERIC
* BOOLEAN / BOOL
* DATE

MVPでは以下は必須対応としない。

* TIMESTAMP
* DATETIME
* JSON
* ARRAY
* STRUCT / RECORD
* その他BigQuery固有型

---

# 8. MVP Profiling Metrics

## テーブル共通

* Record Count

---

## 全column共通

* NULL Count
* NULL Rate

NULL Rate:

`NULL Count / Record Count`

---

## STRING

* NULL Count
* NULL Rate
* Distinct Value Count

MVPでは不要：

* Top Values
* Value Frequency
* Sample Values
* String Length

---

## INTEGER / FLOAT

* NULL Count
* NULL Rate
* Min
* Max

MVPでは不要：

* Mean
* Median
* Percentile
* Histogram
* Standard Deviation

---

## BOOLEAN

* NULL Count
* NULL Rate
* TRUE Count

FALSE Countは、

`Record Count - NULL Count - TRUE Count`

で算出可能。

---

## DATE

* NULL Count
* NULL Rate
* Min Date
* Max Date

---

# 9. Dimension Profiling

MVPの対象とする。DATEとSTRING dimensionのSQL生成・保存・表示に対応する。STRINGはOverallで得たDistinct数が設定上限を超える場合、そのdimensionだけをスキップする。

テーブル全体のprofileだけでなく、

> ユーザーが事前に明示したdimension単位

でも同じprofiling metricsを確認できるようにする。

例：

* event_date
* service
* source

通常2〜3個程度のdimensionを想定する。

全columnを自動的にdimension候補としてprofilingすることはしない。

---

## Dimension指定

dimensionはdbt側の設定に明示的に記述する。

プロジェクト / ディレクトリ単位の設定と、個別model/source単位の設定を可能にする。

dbtの既存設定体系に沿い、

`meta`などの拡張可能な領域にプロダクト固有設定を置く方式を有力候補とする。

イメージ：

```yaml
models:
  my_project:
    marts:
      +meta:
        profiling:
          enabled: true
          dimensions:
            - event_date
```

個別modelでは例えば、

```yaml
models:
  - name: fct_applications
    config:
      meta:
        profiling:
          enabled: true
          dimensions:
            - created_date
            - service
            - source
          max_dimension_values: 10000
```

STRING dimensionの実行可否はabsoluteなDistinct数で判断する。Distinct ratioはカラムの性質を理解するために保存・表示するが、初期実装では自動拒否条件に使わない。判定にはOverall profileの結果を再利用し、専用のBigQuery queryは追加しない。

現在の設定schemaと既定値は[README](../README.md#dbt-projectの取り込みと実行)を参照する。

---

# 10. Profiling対象modelの決定

profiling対象はdbt設定側で制御する。

想定する使い方：

* 特定ディレクトリ配下をまとめてprofiling対象にする
* 特定model/sourceだけ個別にprofiling対象にする
* 必要であれば個別modelで無効化 / 上書きする

現在は`--select`による`unique_id`または一意な名前の完全一致に対応する。dbt selection syntaxへの対応はMVP必須要件とはしない。

---

# 11. Profiling実行方式

リアルタイムprofilingは行わない。

UIを開いたりdimensionを切り替えたりしても、BigQueryへのprofiling queryは発行しない。

理由：

* 巨大テーブルで高コストになり得る
* UI操作によって予期しないBigQuery費用が発生する
* 大規模データほどプロダクトが使いづらくなる

profilingはユーザーが明示的にCLIから実行する。

イメージ：

```bash
<tool> profile
```

dbt run / dbt buildに近い操作感を目指す。

---

# 12. Profileコマンドの処理

概念的には、

1. dbt project / artifactsを読み込む
2. profiling対象model/sourceを決定
3. dimension設定を読み込む
4. BigQuery向けprofiling SQLを生成
5. query costを検証
6. BigQueryでprofiling queryを実行
7. 結果をParquetへ保存
8. UIが新しいprofiling結果を表示する

という流れ。

---

# 13. BigQuery Cost Safety

重要なプロダクト原則：

> 勝手に高コストなqueryを実行しない。

profiling queryに対して、ユーザーが最大処理量を設定できるようにする。これはqueryごとの上限であり、複数relation実行全体の予算上限ではない。

イメージ：

```yaml
profiling:
  max_bytes_billed: 1000000000  # bytes、queryごとの上限
```

上限を超えるqueryは実行しない。

可能であれば実行前に、

* estimated bytes processed
* configured limit

を表示する。

加えて、STRING dimensionには次のcardinality上限を設定できる。

```yaml
profiling:
  max_dimension_values: 10000
```

上限超過時はそのdimensionだけをスキップし、理由、Distinct数、設定上限をCLIへ表示する。他のrelationやdimensionは継続する。

将来的には、

* 危険なGROUP BYの検知
* incremental profiling

なども検討する。

ただしMVPでは過度に複雑な制御は行わない。

---

# 14. Profiling結果の保存方式

MVPでは、

> Storage = Parquet
> Query Engine = DuckDB

を基本構成とする。

---

## Local / OSS

```text
dbt project
    ↓
profile command
    ↓
BigQuery
    ↓
profiling result
    ↓
Parquet
    ↓
DuckDB
    ↓
Web UI
```

profiling結果をローカルのParquetとして保存する。

DuckDBは永続DBとしてではなく、

> Parquetを高速にqueryするためのengine

として利用する。

---

# 15. Profileデータモデル

overall profileとdimension profileを大きく別構造に分けず、共通のprofile relationとして扱う方向を基本とする。

概念例：

```text
model
column
column_type
dimension_name
dimension_value
record_count
null_count
null_rate
distinct_count
min_value
max_value
true_count
profiled_at
```

### Overall profile

```text
dimension_name = NULL
dimension_value = NULL
```

### Dimension profile

```text
dimension_name = "service"
dimension_value = "A"
```

DuckDB側でdimension条件を変えることで、

* Overall
* event_date別
* service別
* source別

を切り替えて表示する。

具体的なParquet schemaと互換性方針は[Profile Storage Schema](profile-storage-schema.md)を参照する。relationは`unique_id`で識別し、同名のmodel/sourceを区別する。dimensionのNULL値もOverallとは別のbucketとして保持する。

---

# 16. Profile履歴

MVPでは履歴を保持しない。

各`profile`実行時に、最新profileを既存profileの代わりとして保存する。

UIで表示するのは常に最新のprofiling結果のみ。

ただし、

* `profiled_at`

は保持し、

> このprofileがいつ計算されたものか

は分かるようにする。

MVPでは以下を行わない。

* 過去profileの閲覧
* snapshot比較
* profile diff
* 時系列比較
* profile履歴管理

これらは将来Data Observability方向へ発展する際に改めて検討する。

---

# 17. Parquetのスケーラビリティ

MVPでは過度に心配しすぎない。

profile結果は元データそのものではなく集約結果なので、通常は元Warehouseより大幅に小さいことを想定する。

まず、

> DuckDB + Parquetで実際に作り、実測する。

必要になった場合に、

* Parquet分割
* partitioned dataset
* dimension cardinality制限
* incremental profiling
* object storageへの移行

などを追加する。

---

# 18. Hosted版への発展

ローカル版とHosted版で根本アーキテクチャを変えないことを目指す。

Local：

```text
Local filesystem
      ↓
   Parquet
      ↓
   DuckDB
```

Hosted候補：

```text
GCS / Object Storage
        ↓
      Parquet
        ↓
      DuckDB
        ↓
      Web UI
```

profile commandなどでGCS上のParquetが更新された場合、新しいParquetを参照することでUIにも最新状態を反映できる構造を想定する。

---

# 19. OSS方針

セルフホスト版を無料・OSSとして提供することを基本方針とする。

重要な原則：

> Self-hosted版そのものが完成された実用プロダクトであること。

Cloudへの誘導のために、

* UIを意図的に弱くする
* 基本profilingを有料化する
* Cloudリンクを大量に表示する

ような設計にはしない。

OSS版だけで、

* catalog UI
* dbt metadata
* profiling
* dimension profiling
* visualization

まで完結させる。

---

# 20. 将来的なHosted版

Hosted版ではOSS版の機能制限解除ではなく、

> 運用の手間を減らすこと

を主な有料価値とする。

将来候補：

* managed hosting
* scheduled profiling
* profile history
* profile diff
* anomaly detection
* alerting
* Slack integration
* GitHub / PR integration
* SSO
* RBAC
* team management

MVPでは実装しない。

---

# 21. MVPではやらないこと

* Profile history
* Profile diff
* Anomaly detection
* Alerting
* AIによる説明
* Business Glossary
* BI integration
* SQL editor
* Semantic Layer管理
* PII detection
* Enterprise Governance
* 高度なRBAC
* dbt Cloud専用機能
* 複数Warehouse対応
* profilingの自動定期実行
* UI操作時のリアルタイムprofiling
* 全columnの自動dimension profiling

---

# 22. MVPの最小Vertical Slice

1. dbt Core projectを読み込める
2. BigQueryに接続できる
3. dbt metadataを取得できる
4. profiling対象model/sourceを設定できる
5. profiling dimensionを設定できる
6. CLIから手動でprofileを実行できる
7. BigQueryの高コストqueryを制限できる
8. 対応型のprofiling metricsを計算できる
9. profiling結果をParquetへ保存できる
10. DuckDBからParquetをqueryできる
11. 1画面のExplorer UIを表示できる
12. schema/dataset/modelを探索・検索できる
13. model詳細画面でcolumn profileを一覧表示できる
14. NULL率などが視覚的に理解できる
15. Overall / dimension別profileを切り替えて表示できる
16. profiling実行日時を確認できる

---

# 23. プロダクト判断原則

機能追加を判断するときは常に、

> この機能はdbtモデルの実データを素早く理解することに役立つか？

を基準にする。

巨大なEnterprise Data Catalogを最初から目指さない。

Data Observability製品を最初から作らない。

まず、

> dbtモデルを開けば、実際にどんなデータが入っているかが一目で分かる。

という体験を十分に良くする。
