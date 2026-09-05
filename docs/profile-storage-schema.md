# Profile Storage Schema

最終更新: 2026-09-05

現在のschema versionは**1**。両ファイルに`schema_version`列を持つ。

MVPのローカル保存は、model metadataとcolumn profileを2つのParquet fileに分ける。

```text
.data-profile/
├── models.parquet
└── column_profiles.parquet
```

APIが返す`ModelProfile`は階層構造だが、ParquetではDuckDBから検索・集計しやすいrelationとして保存する。

## models.parquet

1 modelにつき1 rowを保存する。

| Column | DuckDB type | Null | Description |
|---|---|---|---|
| schema_version | INTEGER | No | 現在は1 |
| unique_id | VARCHAR | No | dbt unique ID。relationの識別子 |
| resource_type | VARCHAR | No | modelまたはsource |
| model_name | VARCHAR | No | model / sourceの表示名。同名を許容 |
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
| schema_version | INTEGER | No | 現在は1 |
| unique_id | VARCHAR | No | `models.parquet.unique_id`への参照 |
| profile_order | INTEGER | No | APIへ再構築する際のprofile表示順 |
| dimension_name | VARCHAR | Yes | OverallではNULL |
| dimension_value | VARCHAR | Yes | OverallまたはdimensionのNULL bucketではNULL |
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

DATE columnがNULLのbucketは`dimension_name="event_date", dimension_value=NULL`として保存する。Overallとはdimension名で区別する。UIでは日付の並び・最新partitionから除外して別表示する。

## 互換性と再生成

version列のない既存ファイルは旧形式として扱う。旧形式は`model_name`参照のため、名前が一意な場合だけ読み込む。読み込める旧データは次の正常な保存でv1になる。同名relationがある旧形式や未対応versionは読み取りを拒否する。

曖昧な旧形式は既存directoryを保持し、別directoryへ`import-dbt`してから`profile`を実行する。結果を確認後、`serve --storage-dir`を新しいdirectoryに変更する。失われたrelationとの対応は推測して移行しない。

`unique_id`未指定の手動データでは`resource_type.database.schema.name`を補完する。保存時はID重複を拒否する。

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

置換前のfilesはstage内へbackupし、途中のfile置換に失敗した場合は両方の復元を試みる。復元が成功した場合はstageを削除して元の例外を通知する。復元にも失敗した場合はstageと両方のbackupを保持し、`StorageRecoveryError`で復旧directoryを通知する。

書き込みは直列化し、更新完了後に読み込む。複数ファイルの同時切替、同時writer、強制終了のtransaction保証はない。世代directoryと参照先切替は未実装。

## 復旧手順

1. `StorageRecoveryError`が発生したら、該当storageの読み書きを止める。
2. 例外の`recovery_dir`にある`models.parquet.backup`と`column_profiles.parquet.backup`を別の安全なdirectoryへコピーして保全する。
3. 書き込み権限やdisk空き容量など、元のエラー原因を解消する。
4. **両方のbackup**を元のstorage directoryの対応するファイルへ戻す。復元中はAPIも停止しておく。
5. `DataProfile.from_storage(path).models()`で読み取りを確認してからAPIを再開する。確認後にstage directoryを削除する。

初回保存などbackup pairが揃わない場合は、stageを保全したまま新directoryへ再import・再profileする。強制終了後に残ったstageも同様に自動復旧対象ではない。
