# Data Profile Roadmap

最終更新: 2026-09-05

プロダクト要件は[product-requirements.md](product-requirements.md)、現在の実装は[development-status.md](development-status.md)を正とする。

## 現在地

```text
[完了] Profiling vertical slice
    ↓
[進行中] 安全な複数relation実行と実環境検証
    ↓
[一部完了] ライブラリ境界・保存契約の安定化
    ↓
[未完了] 再利用可能な0.xライブラリとしての公開準備
```

wheelを生成でき、`DataProfile`を公開入口として利用できる。現在はexperimentalな0.1 APIであり、Phase 1の完了はMVP全要件の完了を意味しない。

## Phase 1: Profiling vertical slice — 完了

- dbt artifact importと`meta.profiling`設定
- BigQuery SQL生成、dry run、queryごとの処理量上限
- Overall / DATE dimension / Missing metricsの保存と表示
- Parquet / DuckDB、FastAPI、React
- 公開Python APIとCLIのplan / run共通化

categorical dimensionは保存・表示まで。実query生成はMVPの残課題として管理する。

## Phase 2: 安全な複数relation実行 — 進行中

実装・ローカルテスト済み:

- 全対象をdry runしてから実queryを開始し、上限超過時は全skip
- relation / dimensionごとのSucceeded / Failed / Skipped
- query・結果変換失敗時のfail-fast、全成功後に1回保存
- 保存・参照・UI選択を`unique_id`で分離、曖昧な名前を拒否
- NULL dimension bucket、結果のカラム・件数・率の検証、取得上限到達時の保存拒否
- 古いplanを実行前に拒否、再import時のprofile互換性判定
- stage生成・読み戻し検証・置換失敗時の復元、復元失敗時のbackup保持
- 個人用artifactに依存しない回帰テスト

完了条件として残るもの:

- 複数の実BigQuery relationで成功・失敗・skip・保存結果を確認する
- NULL partition、空table、複数dimension、結果取得上限を実環境で確認する

## Phase 3: ライブラリ境界と契約の安定化 — 一部完了

完了済み:

- WarehouseAdapterによる推定・query実行の差し替え
- WarehouseAdapterによる対応型・SQL生成・結果変換の差し替え
- ProfileStorageによるimport・load・saveの差し替え
- Parquet schema v1、旧形式の読み取り条件と移行・再生成手順
- DataProfileErrorを基底とする公開例外体系と、旧ProfilingErrorの互換性

残る作業:

- Web serverのrepository契約と公開Storage APIの関係を整理する
- ProfilePlan / ProfileResultのpublic contractを固定する
- config schemaのversioningと非互換変更の方針を決める
- 世代切替・読み取りsnapshot・writer制御により同時アクセスと強制終了を扱う

現在の2ファイル保存は単一transactionではない。通常の失敗に対する復元と、強制終了にも耐えるtransaction保証を区別する。

## Phase 4: 0.xライブラリ公開準備 — 未完了

今回、作業directory外の一時venvへwheelをinstallし、import・同梱sample生成・読み取りを確認した。個人用fixtureを含まないsourceコピーでもunit testが通ることを確認済み。CIでの継続確認と別dbt projectでのE2Eは残る。

- core / Webなどの依存関係と、外部`bq` CLIの前提を整理する
- wheel install・import・sample生成のsmoke testをCIに組み込む
- 別repositoryの最小dbt projectで利用を確認する
- CIでunit test、Web build、package build、install smoke testを実行する
- ブラウザで同名relation選択、Refresh、NULL bucket表示を自動検証する

## 公開判定

1. artifact importからplan・実行・保存まで公開APIで完結する。
2. Warehouse処理とStorage処理を契約に沿って差し替えられる。
3. 成功・失敗・skip・保存完了を構造的に判定できる。
4. 保存の保証範囲・復旧手順と、config / storageの互換性方針が明確である。
5. clean installと別dbt projectでの実行をCI・E2Eで確認できる。

## その後の候補

- categorical query生成、高cardinality制限、結果pagination
- dbt selection syntax、BigQuery Python client adapter
- GCSなどの共有Storage、別Warehouse
- queryの進捗・timeout・job ID、UIのColumn Detail

Alerting、anomaly detection、profile snapshot historyはMVP対象外を維持する。
