# Profile Storage Schema

MVPのローカル保存は、model metadataとcolumn profileを2つのParquet fileに分ける。

```text
fixtures/parquet/
├── models.parquet
└── column_profiles.parquet
```

APIが返す`ModelProfile`は階層構造だが、ParquetではDuckDBから検索・集計しやすいrelationとして保存する。

## models.parquet

1 modelにつき1 rowを保存する。

| Column | DuckDB type | Null | Description |
|---|---|---|---|
| unique_id | VARCHAR | No | dbt unique ID |
| resource_type | VARCHAR | No | modelまたはsource |
| model_name | VARCHAR | No | model / sourceの一意な名前 |
| database_name | VARCHAR | No | database / BigQuery project |
| schema_name | VARCHAR | No | schema / BigQuery dataset |
| relation_name | VARCHAR | No | Warehouse上のrelation名 |
| description | VARCHAR | No | dbt description |
| materialization | VARCHAR | No | table、viewなど |
| tags_json | VARCHAR | No | tagsのJSON array |
| tests_json | VARCHAR | No | dbt testsのJSON array |
| columns_json | VARCHAR | No | profile未生成時にも表示するcolumn metadata |
| profiled_at | VARCHAR | Yes | profile実行日時のISO 8601文字列 |

`profiled_at`はParquetのtimestampへ変換せず、timezone offsetを失わないISO 8601文字列として保存する。dbt metadataだけをimportし、profileがまだ存在しないrelationではNULLにする。

## column_profiles.parquet

1 model × 1 dimension value × 1 columnにつき1 rowを保存する。

| Column | DuckDB type | Null | Description |
|---|---|---|---|
| model_name | VARCHAR | No | `models.parquet`への参照 |
| profile_order | INTEGER | No | APIへ再構築する際のprofile表示順 |
| dimension_name | VARCHAR | Yes | OverallではNULL |
| dimension_value | VARCHAR | Yes | OverallではNULL |
| record_count | BIGINT | No | 対象sliceの行数 |
| column_order | INTEGER | No | dbt上のcolumn表示順 |
| column_name | VARCHAR | No | column名 |
| column_type | VARCHAR | No | 正規化したMVP data type |
| column_description | VARCHAR | No | dbt column description |
| null_count | BIGINT | No | NULL Count |
| null_rate | DOUBLE | No | 0から1のNULL Rate |
| distinct_count | BIGINT | Yes | STRING用 |
| min_value | VARCHAR | Yes | Numeric / DATE用 |
| max_value | VARCHAR | Yes | Numeric / DATE用 |
| true_count | BIGINT | Yes | BOOLEAN用 |

Overall profileは次のように表現する。

```text
dimension_name  = NULL
dimension_value = NULL
```

Dimension profileは、同じ`dimension_name`に対してvalueごとのrowを持つ。

```text
dimension_name  = "created_date"
dimension_value = "2026-08-30"
```

## min_value / max_value

Parquet columnは単一の物理型を必要とするが、Min / MaxはNumericとDATEの両方を格納する。このためMVPではVARCHARとして保存し、repositoryが`column_type`に応じてAPI型へ復元する。

- INT64: `int`
- FLOAT64: `float`
- DATE: ISO date string
- その他: string

将来BigQuery固有型を増やす際に、typed columnsへ分割するかJSON表現へ変更するかを再検討する。

## 書き換え方針

MVPではprofile historyを保持せず、常に最新の2 filesを読み取る。実際のprofile CLIでは、途中状態をUIが読まないように一時directoryへ両方を書き出した後、directory単位で安全に置き換える方針とする。

現在の`build-sample`は開発fixture生成用であり、安全なatomic replacementはまだ実装していない。
