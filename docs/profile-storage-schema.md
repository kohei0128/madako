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
| profiling_json | VARCHAR | No | dbtで解決済みの`meta.profiling`設定 |
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
| empty_string_count | BIGINT | No | STRINGの空文字（`''`）Count |
| missing_count | BIGINT | No | 設定に応じたNULLと空文字の合算Count |
| missing_rate | DOUBLE | No | 0から1のMissing Rate |
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

## NULLと空文字

`meta.profiling.treat_empty_string_as_null`が有効な場合、STRINGでは`missing_count = null_count + empty_string_count`として保存する。無効な場合は`missing_count = null_count`となる。物理的なNULLと空文字は常に別列に保持し、意味を失わないようにする。

この設定が有効なSTRINGのDistinctでは、`NULLIF(column, '')`を使って空文字を除外する。空白だけの文字列は空文字に含めず、将来別設定として扱う。

## min_value / max_value

Parquet columnは単一の物理型を必要とするが、Min / MaxはNumericとDATEの両方を格納する。このためMVPではVARCHARとして保存し、repositoryが`column_type`に応じてAPI型へ復元する。

- INT64: `int`
- FLOAT64: `float`
- DATE: ISO date string
- その他: string

将来BigQuery固有型を増やす際に、typed columnsへ分割するかJSON表現へ変更するかを再検討する。

## 書き換え方針

MVPではprofile historyを保持せず、常に最新の2 filesを読み取る。更新時は同じfilesystem上のstage directoryへ両方を書き出し、DuckDBで読み戻せることを検証してから`os.replace`で置き換える。

置換前のfilesはstage内へbackupし、途中のfile置換に失敗した場合は両方を復元する。これにより通常の生成・置換エラーでは直前の正常なstorageを維持する。OS processがfile間の置換中に強制終了するケースまで単一transactionにするには、将来generation pointer方式を検討する。
