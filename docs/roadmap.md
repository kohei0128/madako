# Data Profile Roadmap

最終更新: 2026-09-06

プロダクト要件は[product-requirements.md](product-requirements.md)、現在の実装は[development-status.md](development-status.md)を正とする。

## 現在地

```text
[完了] Profiling vertical slice
    ↓
[進行中] 安全な複数relation実行と実環境検証
    ↓
[完了] ライブラリ境界・保存契約の安定化
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

- 複数の実BigQuery relationで失敗・skip・保存しない結果を確認する
- NULL partition、空table、複数dimension、結果取得上限を実環境で確認する

2026-09-06にtsuboの3 relationを実BigQueryで同時にprofileし、全項目の成功とParquet保存を確認した。保存後はPython APIとHTTP APIから`unique_id`ごとに読み戻した。実環境データを使った失敗・skipと、境界条件の確認は引き続き残る。

## Phase 3: ライブラリ境界と契約の安定化 — 完了

完了済み:

- WarehouseAdapterによる推定・query実行の差し替え
- WarehouseAdapterによる対応型・SQL生成・結果変換の差し替え
- ProfileStorageによるimport・load・saveの差し替え
- Parquet schema v1、旧形式の読み取り条件と移行・再生成手順
- DataProfileErrorを基底とする公開例外体系と、旧ProfilingErrorの互換性
- **ProfileStorageにget_modelメソッドを追加し、Web serverを統一** (2026-09-05)
- **ProfilePlan/ProfileResult/ProfilePlanItem/ProfileItemResultをPydantic Modelに移行** (2026-09-05)
  - frozen=True でimmutableを保持
  - Field descriptionsによるドキュメント強化
  - 全テスト通過を確認
- **Config Versioningルールを文書化** ([config-versioning.md](config-versioning.md))
  - ProfilingConfig/ModelProfile.profiling_signature()にdocstring追加
  - Parquet schema versioningのドキュメント追加
  - バージョニング方針と互換性ルールを明文化
- **世代ディレクトリ方式の実装（実験的機能）** (2026-09-05)
  - 環境変数 `DATA_PROFILE_USE_GENERATIONS=1` で有効化
  - symlinkによるatomic切り替えで並行アクセス安全性を向上
  - 複数世代の保持とロールバック機能
  - 7つの新規テストで動作検証済み
  - 既存の直接保存方式と共存可能

Phase 3は設計・実装・テストまで完了。0.xライブラリとしての契約が安定化。

## Phase 4: 0.xライブラリ公開準備 — 未完了

今回、作業directory外の一時venvへwheelをinstallし、import・同梱sample生成・読み取りを確認した。個人用fixtureを含まないsourceコピーでもunit testが通ることを確認済み。CIでの継続確認と別dbt projectでのE2Eは残る。

- core / Webなどの依存関係と、外部`bq` CLIの前提を整理する
- wheel install・import・sample生成のsmoke testをCIに組み込む
- 別repositoryの最小dbt projectで利用を確認する
- CIでunit test、Web build、package build、install smoke testを実行する
- ブラウザで同名relation選択、Refresh、NULL bucket表示を自動検証する

## 公開判定

1. ✅ artifact importからplan・実行・保存まで公開APIで完結する。
2. ✅ Warehouse処理とStorage処理を契約に沿って差し替えられる。
3. ✅ 成功・失敗・skip・保存完了を構造的に判定できる（ProfileResultはPydantic Model）。
4. ✅ 保存の保証範囲・復旧手順と、config / storageの互換性方針が明確である（config-versioning.md参照）。
5. ⏳ clean installと別dbt projectでの実行をCI・E2Eで確認できる → Phase 4へ継続

**Phase 3完了により、条件1-4を満たした。残るはPhase 4のCI/E2E整備のみ。**

## その後の候補

- categorical query生成、高cardinality制限、結果pagination
- dbt selection syntax、BigQuery Python client adapter
- GCSなどの共有Storage、別Warehouse
- queryの進捗・timeout・job ID、UIのColumn Detail

Alerting、anomaly detection、profile snapshot historyはMVP対象外を維持する。
