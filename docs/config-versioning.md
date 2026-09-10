# Config and Storage Versioning Guide

最終更新: 2026-09-10

このドキュメントは、ProfilingConfigとParquet storageのバージョニング方針を定義します。

## 概要

Madakoには3層のバージョニングがあります:

1. **Parquet Schema Version**: ファイル形式のバージョン（カラム構造、型）
2. **Profile Result Signature**: 計算結果に影響する設定の変更検知
3. **Profile Computation Version**: 対応型やmetric計算実装の変更検知

`[storage]`の`mode`と`keep_generations`は保存directoryの公開・保持方法だけを制御し、Parquet schema versionやprofiling signatureには影響しない。設定を省略した既存configはdirect modeとして引き続き利用できる。

## 1. Parquet Schema Version

### 現在のバージョン

- **Version 1** (Current)
  - `schema_version` カラムを含む
  - `unique_id` による明確なモデル識別
  - `profiling_json` によるconfig保存
  - optionalな`profile_version`による計算version保存
  - optionalな`upstream_ids_json`によるdirect lineage保存
  - `empty_string_count`, `missing_count`, `missing_rate` による詳細な欠損値追跡
  - optionalな`distinct_ratio`によるSTRINGのDistinct比率保存

### バージョニングルール

| 変更内容 | 対応 |
|---------|------|
| オプショナルカラムの追加 | 後方互換 - 旧バージョンは新カラムを無視 |
| カラムの型変更 | 非互換 - 新schema versionが必要 |
| カラムの削除 | 非互換 - 新schema versionが必要 |
| 未対応schema_versionの読み取り | エラー - 移行手順を提示 |
| Legacy形式（version列なし）の読み取り | 許可（警告付き）- 次回保存でv1に変換 |

### 新バージョン追加時の手順

1. `_write_profile_storage_files()` で新schema versionを使用
2. `DuckDBProfileRepository._schema()` で新versionをサポート
3. `DuckDBProfileRepository._load()` で新カラムを処理
4. 旧versionからの読み取りパスを保持
5. マイグレーションガイドを docs/ に追加

## 2. ProfilingConfig Versioning

### フィールドの種類

ProfilingConfigのフィールドは影響範囲によって分類されます:

#### 計算ロジックに影響するフィールド

これらのフィールド変更は `profile_result_signature()` を変更し、既存profileを無効化します:

- `treat_empty_string_as_null`: missing_count/missing_rate の計算に影響
- `dimensions`: どの次元で分割するか
- `max_dimension_values`: 保存するSTRING dimensionの有無に影響
- （将来追加される計算パラメータ）

#### 実行制御フィールド

これらのフィールド変更は signature に影響しません:

- `enabled`: profiling の有効/無効
- `max_bytes_billed`: コスト制限（planの実行可否に影響するが、結果には影響しない）

### 互換性のあるフィールド追加

```python
class ProfilingConfig(BaseModel):
    enabled: bool = False
    dimensions: list[str] = Field(default_factory=list)
    max_dimension_values: Annotated[int, Field(gt=0)] = 10_000
    max_bytes_billed: Annotated[int, Field(gt=0)] | None = None  # "1 GB"等も入力可能
    treat_empty_string_as_null: bool = False
    # 新規追加（デフォルト値あり）
    trim_whitespace: bool = False  # 将来の機能例
```

**影響**:
- 既存データの読み込み: デフォルト値で補完される
- 保存時: 新しいフィールドを含めて保存
- `profiling_signature()` に影響しなければ、既存profileを引き継ぐ

### 非互換なフィールド変更

計算ロジックに影響する変更例:

```python
# treat_empty_string_as_null を True に変更した場合
```

**挙動**:
1. `profile_result_signature()` が変わる
2. `import_dbt_profiles()` で既存profileと比較
3. signatureが不一致 → profileと profiled_at をクリア
4. ユーザーは再度 `profile()` を実行

## 3. Plan SignatureとProfile Result Signature

### 目的

`ModelProfile.profiling_signature()` は、作成済みplanを無効化する入力フィールドのsignatureを提供します。`enabled`や`max_bytes_billed`を含むため、実行条件が変わった古いplanは拒否されます。

`ModelProfile.profile_result_signature()` は、保存済みprofileの計算結果と互換性があるかを判定します。`enabled`と`max_bytes_billed`は実行制御だけに使われるため除外され、これらを変更してdbt artifactを再importしても既存profileを保持します。

両signatureには現在の`PROFILE_COMPUTATION_VERSION`も含む。対応型や計算SQLなど、artifactやProfilingConfigに現れない計算変更でも古いplanやprofileを識別できるようにする。

### 含まれるフィールド

```python
{
    "unique_id",        # モデル識別
    "resource_type",    # model / source
    "name",             # モデル名
    "database",         # データベース名
    "schema_name",      # スキーマ名
    "relation_name",    # テーブル/ビュー名
    "materialization",  # table / view / incremental等
    "columns",          # カラム定義（name, type, description）
    "profiling",        # Plan signatureではProfilingConfig全体
}
```

Profile Result Signatureでは、`profiling`から`enabled`と`max_bytes_billed`を除外します。

### 除外されるフィールド

- `profiles`: 計算結果そのもの
- `profiled_at`: 実行タイムスタンプ
- `tags`, `tests`: 計算に影響しないメタデータ
- `description`: モデルの説明文
- `upstream_ids`: 表示用lineage metadata

### 使用箇所

1. **Plan作成時**: `ProfilePlanItem.model_signature` に保存
2. **Plan実行時**: 現在のmodelのPlan Signatureと比較し、staleなplanを拒否
3. **dbt import時**: `import_dbt_profiles()` でProfile Result Signatureを既存profileと比較

### 変更シナリオ例

| シナリオ | Signature変化 | 既存Profile |
|---------|--------------|-------------|
| カラムを追加 | 変わる | クリア |
| `treat_empty_string_as_null` 変更 | 変わる | クリア |
| `max_bytes_billed` 変更 | 変わらない | 保持 |
| `enabled` 変更 | 変わらない | 保持 |
| description のみ変更 | 変わらない | 保持 |
| tag 追加 | 変わらない | 保持 |

## 4. Profile Computation Version

現在のversionは**4**。version 2でNUMERIC / BIGNUMERICのMin / Max profilingを追加し、version 3でSTRING dimensionとDistinct ratio、version 4でDATETIME / TIMESTAMPのMin / Maxとdimension profilingを追加した。

profile保存時に`models.parquet.profile_version`へ記録する。dbt artifactの再import時は、保存済みversionが現在の`PROFILE_COMPUTATION_VERSION`と一致する場合だけ既存profileを引き継ぐ。`profile_version`列がない既存v1 storageはversion 1として読み取るため、今回の変更後は再profileされる。

`distinct_ratio`は既存schema version 1へのoptional列追加として扱う。列がない既存storageは読み取り時にDistinct数と行数から補完するため、schema version変更は不要。

## 5. 実装リファレンス

### 関連ファイル

- `src/data_profile/models.py`: ProfilingConfig, ModelProfile定義
- `src/data_profile/storage.py`: Parquet schema version管理
- `src/data_profile/repository.py`: Schema version検証
- `docs/phase3-contracts-design.md`: 設計方針の詳細

### 関連テスト

- `tests/test_storage.py::test_unknown_schema_version_is_rejected`
- `tests/test_storage.py::test_legacy_storage_migrates_only_when_names_are_unambiguous`
- `tests/test_storage.py::test_storage_without_profile_version_loads_as_version_one`
- `tests/test_public_api.py::test_reimport_preserves_only_compatible_profiles`
- `tests/test_public_api.py::test_reimport_invalidates_profiles_from_old_computation_version`

## 6. よくある質問

### Q: 新しいProfilingConfigフィールドを追加したい

A: 以下を確認してください:
1. デフォルト値を提供する（後方互換性）
2. 計算ロジックに影響するか判断する
3. 影響する場合、`profiling_signature()` のincludeに含まれることを確認
4. テストを追加（新フィールドありの保存→読み取り、signature変化の確認）

### Q: Parquetのカラムを追加したい

A: 以下を確認してください:
1. オプショナルカラムとして追加（NULL許可）
2. `_write_profile_storage_files()` で新カラムを書き込む
3. `DuckDBProfileRepository._load()` で新カラムを読み取る（存在チェック）
4. 旧データ（新カラムなし）も読めることをテストで確認

### Q: 非互換な変更をしたい（型変更、カラム削除）

A: 以下の手順を踏んでください:
1. schema versionを 2 に上げる
2. v1からv2へのマイグレーションロジックを実装
3. v1の読み取りサポートを維持（移行期間）
4. マイグレーションガイドを作成
5. ユーザーに通知（breaking change）

## 7. 今後の拡張

### 候補機能とバージョニング影響

| 機能 | ProfilingConfig変更 | Signature / computation version影響 | Parquet影響 |
|-----|-------------------|--------------|------------|
| trim_whitespace | 新フィールド追加 | Yes（計算ロジック） | No |
| カテゴリカルpagination | 新フィールド追加 | No（表示制御） | No |
| 高cardinality制限 | 新フィールド追加 | Yes（結果に影響） | No |
| 対応型追加 | なし | computation versionを更新 | 通常No |
| 新しいmetric追加 | なし | computation versionを更新 | Yes（新カラム） |

### バージョニング方針（0.x系 vs 1.0以降）

- **0.x系** (Current): Experimental API
  - 非互換変更を許容（マイナーバージョンでも）
  - 変更時はCHANGELOGで通知
  - 重大な変更時はマイグレーションガイド提供

- **1.0以降** (Future): Semantic Versioning
  - MAJOR: 非互換変更（schema version変更など）
  - MINOR: 後方互換な機能追加（新ProfilingConfigフィールドなど）
  - PATCH: バグフィックス
