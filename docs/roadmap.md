# Data Profile Roadmap

最終更新: 2026-09-05

この文書は、現在のアプリを再利用可能なPythonライブラリへ育てるまでの順序と完了条件を示す。プロダクト要件は[product-requirements.md](product-requirements.md)、実装済み機能の詳細は[development-status.md](development-status.md)を正とする。

## 現在地

```text
[完了] Vertical slice
  dbt artifacts → BigQuery profile → Parquet/DuckDB → API → Web UI
      ↓
[進行中] 安全な複数relation実行
      ↓
[次] Warehouse / Storage境界の安定化
      ↓
[次] 0.xライブラリとして公開可能
      ↓
[将来] 別warehouse・外部storage・運用機能
```

Python wheelは既に生成でき、`DataProfile`を公開入口として利用できる。ただし現時点では、同一repository内で試すためのexperimentalな0.1 APIという位置付けである。

## Phase 1: Profiling vertical slice — 完了

- dbt models / sources / columns / testsのartifact取り込み
- `meta.profiling`による対象・dimension・処理量上限の設定
- BigQuery SQL生成、dry run、上限判定、実行
- Overall、DATE dimension、Missing metricsの保存と表示
- Parquet / DuckDB repository、FastAPI、React UI
- `DataProfile.plan()` / `run()` / `profile()`とCLIの共通化

## Phase 2: 安全な複数relation実行 — 進行中

完了済み:

- 全対象をdry runしてから実queryを開始
- 上限超過時は全実行を停止
- 全query成功後にstorageを1回だけ更新
- Parquetをstageへ生成して読み戻し検証
- file置換失敗時の既存storage復元
- relation / dimension単位の`Succeeded / Failed / Skipped`結果
- fail-fast：query・結果変換失敗時は後続skip、保存なし
- Python APIの実行summaryとCLIの失敗終了コード

残り:

- 複数の実relationを使ったend-to-end確認

## Phase 3: ライブラリ境界の安定化

Storage境界も実装済み。`ProfileStorage`を公開し、既定の`ParquetProfileStorage`をAPIの取込・読み込み・保存で共用する。保存先差し替え、query失敗時の保存抑止、不完全なParquet pairの検出をテストした。現在はローカル2ファイルの契約で、共有storageや世代単位の切替は未対応。

接続・推定・query実行の`WarehouseAdapter`と既定の`BigQueryAdapter`を追加済み。公開APIの`adapter=`で差し替えられ、BigQueryなしの失敗・成功混在テストまで完了した。SQL生成と結果変換はBigQuery固有のままで、別warehouseへの対応は未実装。

- `bq` subprocessを`WarehouseAdapter` interfaceの背後へ移す
- BigQuery adapterの認証、dry run、query実行を単体テスト可能にする
- 完了：Parquet保存を`ProfileStorage` interfaceの背後へ移す
- `ProfilePlan` / `ProfileResult` / 例外のpublic contractを固定する
- CLIとWeb serverがpublic APIだけを利用する状態にする

## Phase 4: 0.xライブラリ公開準備

- config schemaとstorage schemaへversionを付ける
- 非互換変更時のmigrationまたは明確な再生成手順を用意する
- BigQuery依存をoptional dependencyとして整理する
- clean environmentでwheel installとimportを検証する
- 最小quickstart、設定リファレンス、例外・cost safetyを文書化する
- CIでunit test、package build、install smoke testを実行する

ここまで完了したら、別repositoryのdbt projectから利用する0.xライブラリとして扱える。

## ライブラリ化へ進める判定条件

以下がすべて満たされた時点を、experimentalな内部APIから再利用可能な0.xライブラリへ進む境界とする。

1. `DataProfile.profile()`だけでartifact取り込み後のplan・実行・保存が完結する
2. BigQuery固有処理がadapterへ分離され、テストでは差し替えられる
3. 複数relationの成功・失敗・skipを呼び出し側が構造的に判定できる
4. profile失敗や保存失敗で直前の正常なstorageを失わない
5. public API、config、storage schemaにversioning方針がある
6. wheelをclean environmentへinstallし、サンプルdbt projectで動作確認できる

現在は1と3、および2の接続境界まで実装済み。4は通常の書き込み・置換エラーに対応しているが、複数fileの同時読み取りやprocess強制終了に対するtransaction保証は未完了。次はStorage境界とこの保証範囲を整理し、5と6を整備する。実relationでの複数対象確認も残る。

## 公開後の候補

- dbt selection syntaxへの対応
- BigQuery Python client adapter
- GCSなどの共有storage
- Snowflake、Databricksなどのwarehouse adapter
- 高cardinality dimensionの制限・sampling
- profile実行状況のUI表示

Alerting、anomaly detection、profile snapshot historyは引き続きMVPの対象外とする。
