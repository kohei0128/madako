# Data Profile

dbt metadataと実データのprofiling結果を同じ画面で確認する、ローカルファーストのデータカタログです。現在はexperimentalな0.1 Python APIとWeb UIを提供します。

[要件定義](docs/product-requirements.md) / [開発状況](docs/development-status.md) / [Roadmap](docs/roadmap.md) / [Storage Schema・復旧手順](docs/profile-storage-schema.md)

## Quickstart

Python 3.12以上、uv、Node.js / npmを使用します。サンプルはパッケージ内の合成データで、dbtやBigQueryの接続は不要です。repository内での`uv sync`はテストとWeb serverを含む開発用依存関係を導入します。

```bash
cd app/data_profile
UV_CACHE_DIR=.uv-cache uv sync
UV_CACHE_DIR=.uv-cache uv run data-profile build-sample
UV_CACHE_DIR=.uv-cache uv run data-profile serve
```

別ターミナルでWebを起動します。

```bash
cd app/data_profile/web
npm ci
npm run dev
```

UI: `http://localhost:5173`、API仕様: `http://127.0.0.1:8000/docs`。

wheelから利用する場合、Python APIとsample・dbt importにはcoreだけを、`serve`も使う場合はWeb extraを導入します。Node.js / npmはReact UIの開発・build時だけ必要です。

```bash
pip install data-profile
pip install 'data-profile[web]'
```

既定の保存先は実行directoryの`.data-profile/`です。`build-sample --output-dir`と`serve --storage-dir`で変更できます。独自JSONから生成する場合は`build-sample --source <file>`を使います。Webのbuild成果物をAPI serverが配信する機能はありません。

## dbt projectの取り込みと実行

事前にdbt側で`target/manifest.json`を生成してください。`catalog.json`があればカラム順・型に使用し、なければmanifestへfallbackします。dbtのparse / buildは暗黙に実行しません。

```bash
uv run data-profile import-dbt --project-dir <dbt-project> --output-dir .data-profile
uv run data-profile plan --storage-dir .data-profile --select model.my_project.events \
  --project <billing-project> --location <location> --show-sql
uv run data-profile profile --storage-dir .data-profile --select model.my_project.events \
  --project <billing-project> --location <location>
```

BigQueryのplan / profileにはインストール・認証済みの`bq` CLIが必要です。`--project`省略時はrelationのdatabase、`--location`省略時は`asia-northeast1`を使います。

`--select`省略時はenabledな全relationを対象にします。指定できるのは完全一致の`unique_id`または一意な名前です。同名relationがある場合は`unique_id`を使ってください。dbt selection syntaxには未対応です。

設定はdbt resourceの`config.meta.profiling`で宣言します。

```yaml
config:
  meta:
    profiling:
      enabled: true
      dimensions: [event_date]
      max_bytes_billed: 1000000000
      treat_empty_string_as_null: true
```

| 設定                       | 既定値     | 意味                                            |
| -------------------------- | ---------- | ----------------------------------------------- |
| enabled                    | false      | profiling対象に含める                           |
| dimensions                 | []         | 空ならOverallのみ。現在のquery生成はDATEのみ    |
| max_bytes_billed           | 1000000000 | 正の整数bytes、queryごとの上限                  |
| treat_empty_string_as_null | false      | STRINGの`''`をMissingへ算入し、Distinctから除外 |

空白だけの文字列は空文字に含めません。NULLと空文字の件数は別々に保存します。設定・カラム・relationなどが変わった状態で再importした場合、以前のprofileはクリアされるため再実行してください。

OverallとDATE dimensionを生成できます。categorical dimensionは保存済みデータの表示のみ対応しています。DATEのNULL bucketは日付比較から分離して表示します。UIのRefreshで更新後のstorageを読み直せます。UI操作はBigQuery queryを発行しません。

対応型はSTRING、INT64、FLOAT64、NUMERIC、BIGNUMERIC、BOOL、DATEです。NUMERIC / BIGNUMERICのMin / Maxは精度を失わない10進文字列としてAPIとParquetへ保存します。

## Python API

```python
from data_profile import DataProfile

app = DataProfile.from_dbt_project(
    project_dir="path/to/dbt-project",
    storage_dir=".data-profile",
)
plan = app.plan(select="model.my_project.events", location="asia-northeast1")
for item in plan.items:
    print(item.model.unique_id, item.estimated_bytes, item.executable)

result = app.run(plan)
for outcome in result.items:
    print(outcome.item.model.unique_id, outcome.item.dimension,
          outcome.status, outcome.row_count, outcome.error)
print(result.successful, result.storage_updated)
```

既存storageを使う場合は`DataProfile.from_storage(path)`、planとrunをまとめる場合は`app.profile(select=...)`を使用します。plan後に対象のschema・設定が変わった場合やplan内のmodelを書き換えた場合は、再planが必要です。

- 全対象をdry runしてから実queryを開始します。いずれかが上限超過なら全項目をskipします。上限はqueryごとであり、実行全体の予算ではありません。
- query・変換失敗ではfail-fastとなり、今回の結果は保存しません。
- カラム不足・重複、件数・率の不整合を拒否します。取得上限100,000 metric rowsに到達した場合も保存を拒否します。
- `succeeded`はqueryと変換の成功、`row_count`は取得したmetric rows数です。
- `storage_updated`は保存完了を示します。`profiled_models`は保存できたmodelの表示名です。同名relationの識別には`items[].item.model.unique_id`を使います。
- 設定・dry-run・古いplan・保存のエラーは公開例外で通知します。query／変換の失敗は`ProfileResult`に格納します。
- CLIのprofileは実行失敗・skip時に終了コード1を返します。

`adapter=`には`WarehouseAdapter`を実装するオブジェクトを指定できます。完全なadapterは`supported_types`、`build_profile_query()`、`estimate()`、`execute()`、`parse_profile_rows()`を持ち、SQL生成から結果変換までを所有します。既定の`BigQueryAdapter`はBigQuery SQLと`bq` CLIを使用します。

0.1の互換性のため、`estimate()` / `execute()`だけを持つ既存adapterも利用できます。この場合、SQL生成・結果変換は`BigQueryAdapter`で補完されます。`estimator=` / `runner=`は対応する実行関数だけを上書きし、生成・変換には選択したadapterを使います。

公開例外の基底は`DataProfileError`です。用途別に`ArtifactError`、`PlanningError`、`WarehouseError`、`ResultValidationError`、`StorageError`を公開しています。従来の`ProfilingError`はplanning・warehouse・result validationをまとめて捕捉する互換用の基底です。保存実装の予期しない失敗は`StorageOperationError`へ包み、元の例外を`__cause__`に保持します。

`storage=`には`ProfileStorage`の`paths / exists / load / save`を実装するオブジェクトを指定できます。既定は`ParquetProfileStorage`で、独自storageは取込・plan・保存とWeb serverで共用されます。現在の契約はローカル2ファイルのパスを含みます。

## 保存と復旧

Parquet schema v1は`unique_id`でrelationを識別します。旧形式は名前が一意な場合に読み込め、次回保存でv1になります。同名relationを含む旧形式は新directoryへ再import・再profileしてください。既存storageを消す必要はありません。

書き込みはstage生成・読み戻し検証後に2ファイルを順番に置換します。置換失敗時は復元し、復元にも失敗した場合は`StorageRecoveryError.recovery_dir`にbackupを残します。詳細は[復旧手順](docs/profile-storage-schema.md#復旧手順)を参照してください。

同時更新・更新中の読み取り・強制終了に対するtransaction保証はありません。書き込みを直列化し、更新完了後に読み込んでください。

## Test

```bash
uv run pytest
uv build
cd web
npm ci
npm run build
npx playwright install --with-deps chromium
npm run test:e2e
```

unit testは合成artifactと一時directoryを使い、実BigQueryや個人用dbt projectに依存しません。Playwrightは同名relationの選択、Refresh、DATEのNULL bucket表示を検証します。

`.github/workflows/data-profile.yml`はPython 3.12 / 3.13のunit test、Web build、Playwright、package buildを実行します。package smokeではcore wheelだけを一時venvへinstallし、import・sample生成・読み取りに加えて、`e2e/minimal_dbt_project`を作業directory外へコピーして公開APIから実行します。

Phase 2のBigQuery E2Eを再実行する場合は、一意な検証用dataset名を指定します。スクリプトはdatasetと検証tableを作成し、完了時にdatasetを削除します。tableには24時間の既定有効期限も設定します。

```bash
uv run python examples/verify_phase2_bigquery.py \
  --project <billing-project> \
  --dataset data_profile_phase2_<unique-name> \
  --location asia-northeast1
```

## HTTP API

- `GET /api/health`: processの応答確認。storageの健全性確認ではありません。
- `GET /api/models`: profileを含む全relation。`?include_profiles=false`でmetadataだけ取得。
- `GET /api/models/{identifier}/profile`: `unique_id`または一意な名前で取得。未検出は404、曖昧な名前は409。

## このrepositoryの実環境用例

`examples/profile_tsubo.py`はローカルのtsubo project用です。既定ではenabledな全relationのdry runだけを行います。対象を限定する場合は`--select`、実queryを実行する場合は`--execute`を指定します。利用前にdbt artifactsを更新してください。

UIにtsuboのprofileを反映する
```
uv run data-profile serve --storage-dir /tmp/tsubo-profile
```
