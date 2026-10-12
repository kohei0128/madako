# Getting started with Madako

[日本語版](../ja/getting-started.md) · [Documentation index](../README.md) · [Project README](../../README.md)

Madako is a local-first data catalog that displays dbt metadata and profiles of the underlying data in one browser UI. It currently supports BigQuery and is experimental software in the 0.1 series.

## Requirements and installation

Madako requires Python 3.11 or later. Install the CLI, Python API, and Web UI in an isolated environment with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install madako
```

To use an existing Python environment instead:

```bash
python -m pip install madako
```

This package includes the Python API, CLI, and prebuilt Web UI. Profiling real BigQuery data additionally requires the Google Cloud SDK `bq` CLI to be installed and authenticated.

## Try the Web UI with sample data

The bundled sample uses synthetic data and does not require dbt, BigQuery, or the `bq` CLI.

```bash
madako demo
```

Open <http://127.0.0.1:8000>. Madako uses temporary sample storage for the demo and removes it when the server stops. The local API documentation is available at <http://127.0.0.1:8000/docs>.

## Use Madako with your dbt project

### 1. Choose what to profile

Add `meta.profiling` to the YAML entry for each model or source you want to profile:

```yaml
models:
  - name: events
    config:
      meta:
        profiling:
          enabled: true
          dimensions: [event_date, service]
          max_dimension_values: 10000
          treat_empty_string_as_null: true
```

After changing profiling configuration, refresh the dbt artifacts with `dbt docs generate` or `dbt parse` before running `madako profile` again.

If `dimensions` is omitted, Madako only calculates an overall profile. A dimension may be a DATE, DATETIME, TIMESTAMP, or STRING column. Madako skips an individual STRING dimension when its overall distinct count exceeds `max_dimension_values`, which defaults to 10,000. The distinct ratio is shown in the UI but does not control whether the dimension runs.

With `treat_empty_string_as_null: true`, empty strings (`''`) in STRING columns are included in the Missing metric alongside NULL values. The default is `false`, which counts only NULL as Missing. Strings containing only whitespace, such as `' '`, are not empty strings.

Set `max_bytes_billed` on a model or source to limit the total billed bytes for that relation in one profiling run. Madako dry-runs Overall and every configured dimension query and compares their **sum** with the limit. If any relation exceeds its budget, no real query in the entire plan starts. A shared limit in `madako.toml` also enables this check for relations without a YAML limit. Relations skip the dry run only when neither limit is set.

For example, a `"1 GB"` limit blocks two queries estimated at 600 MB each, even though each query individually fits. `madako plan` shows each query estimate, the relation total, and its budget.

All queries for a relation share one budget. For example, with a 1 GB limit, if the first query uses 600 MB, later queries have 400 MB left. Estimates can differ from actual usage, so profiling stops if the remaining budget is insufficient, even when the preflight estimate was within the limit.

```yaml
config:
  meta:
    profiling:
      enabled: true
      max_bytes_billed: "1 GB"
```

`max_bytes_billed` also accepts an integer number of bytes. Human-readable values may use `KB`, `MB`, `GB`, and `TB` for decimal units or `KiB`, `MiB`, `GiB`, and `TiB` for binary units.

To profile a subset of rows, set `where` on a model or source. This example uses `event_date` to select rows dated seven days ago or later:

```yaml
config:
  meta:
    profiling:
      enabled: true
      where: "event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)"
      dimensions: [event_date, service]
      max_bytes_billed: "5 GiB"
```

`where` is a BigQuery SQL condition without the `WHERE` keyword. Madako trims leading and trailing whitespace and rejects empty conditions; BigQuery validates SQL syntax and column names during the dry run or execution. Omit `where` or set it to `null` to profile all rows. The same condition applies before aggregation in Overall and every dimension, so all metrics describe the selected rows. Use a condition that selects the same rows across queries; non-deterministic expressions such as `RAND()` can cause dimension row counts to disagree with Overall.

Selecting fewer rows does not necessarily reduce processed bytes by the same proportion. When `max_bytes_billed` is set, Madako estimates usage for the filtered queries. Inspect the condition and estimate with `madako plan`, or add `--show-sql` to see the generated queries.

The Web UI labels filtered results as `Filtered`, shows the condition, and displays `profiled rows`. Overall represents all selected rows. Changing, adding, or removing the condition invalidates saved profiles on the next dbt import; re-profiling refreshes the results. A condition using `CURRENT_DATE()` is evaluated at query time, while saved results remain associated with their displayed profiling timestamp. Sampling is not supported.

### 2. Configure Madako

Create `madako.toml` in the root of your dbt project:

```toml
[project]
dbt_project_dir = "."
storage_dir = ".madako"

[bigquery]
project = "your-gcp-project"
location = "asia-northeast1"
threads = 4

[profiling]
max_bytes_billed = "1 GB"

[server]
host = "127.0.0.1"
port = 8000

[storage]
mode = "generations"
keep_generations = 3
```

Relative paths are resolved from the directory containing `madako.toml`. Madako searches for this file from the current directory upward, so commands can run from anywhere inside the project. Use a different file with `madako --config path/to/madako.toml <command>`.

`profiling.max_bytes_billed` sets a shared upper limit for every enabled model and source. The limit applies separately to each relation in one run, including its Overall query and all dimension queries. If a relation also sets `max_bytes_billed` in YAML, Madako uses the smaller limit: with a shared limit of `"1 GB"`, YAML `"500 MB"` uses 500 MB and YAML `"2 GB"` uses 1 GB. Omitting the shared limit leaves the YAML limit in effect. Both settings accept integer bytes or a size such as `"1 GB"` or `"1 GiB"`.

To inspect each relation's total estimate and applied limit, refresh the saved metadata and view the plan:

```bash
madako import-dbt
madako plan
```

If any relation is over its limit, the entire plan stops before executing queries and reports the relation and estimate. Adjust that relation's `where` condition to narrow the rows, regenerate the dbt artifacts, and try again.

`bigquery.threads` is the maximum number of relations profiled concurrently and defaults to 4. Different relations can run in parallel. Within one relation, Madako completes the Overall query before running dimension queries sequentially. Override the value for one run with `madako profile --threads 8`.

With `storage.mode = "generations"`, Madako writes and validates a complete new generation before atomically switching the `current` symlink. The current generation is included in `keep_generations`, which defaults to 3 and must be at least 2. This mode assumes a local filesystem and a single writer; it does not guarantee writes from multiple processes. If `[storage]` is omitted, Madako uses direct mode. `DATA_PROFILE_USE_GENERATIONS=1` remains available for compatibility, but new configurations should use `madako.toml`.

Generation mode can read an existing direct-mode directory. The next successful save creates the generation layout; until then Madako continues to read the two existing Parquet files.

Add `.madako/` to the dbt project's `.gitignore` if generated storage should not be committed.

### 3. Generate artifacts, profile, and browse

Generate dbt artifacts before running Madako:

```bash
dbt docs generate
madako profile
madako serve
```

For a development loop that only changes YAML descriptions, tests, or `meta.profiling`, `dbt parse` is faster:

```bash
dbt parse
madako profile
```

`dbt parse` updates only `manifest.json`; it does not refresh the column types or ordering that `dbt docs generate` writes to `catalog.json`. If an existing catalog is stale, Madako warns and uses it. Without a catalog, Madako uses YAML `data_type` values. Columns without type information in either place become `UNKNOWN` and are excluded from profiling. Run `dbt docs generate` after changing physical columns, on the first import, or whenever accurate warehouse types are required.

`madako profile` imports `manifest.json` and `catalog.json` into storage before profiling BigQuery. It does not run a dbt command for you.

By default, the terminal shows Import, Plan, Profile, and Save progress plus a summary. Interactive terminals update progress in place; redirected output and CI append lines without ANSI cursor control. Use `madako profile --verbose` (or `-v`) for dry runs, query starts and completions, and bytes processed per query. Failures and dimensions skipped for cardinality are visible without verbose mode; verbose mode also explains queries not run after a failure. The summary counts successful queries separately from skipped queries. `TERM=dumb` disables line rewriting and color, so `NO_COLOR` does not otherwise change the content.

## Command overview

| Command | Purpose |
| --- | --- |
| `madako demo` | Start the local browser UI with temporary sample data |
| `madako profile` | Import dbt artifacts, profile enabled relations, and save the results |
| `madako serve` | Open a saved catalog in the local browser UI |
| `madako import-dbt` | Refresh dbt metadata without connecting to BigQuery |
| `madako plan --show-sql` | Inspect estimates and generated SQL from saved metadata |
| `madako build-sample` | Create synthetic storage for trying the UI |

Command-line options can temporarily override configuration. Discover them with:

```bash
madako --help
madako profile --help
```

## Query safety

- Madako dry-runs queries when a shared or YAML `max_bytes_billed` limit applies.
- If the sum of a relation’s dry-run estimates exceeds its limit, Madako starts no real query in the entire plan. During execution, all queries for that relation share the remaining billed-byte budget.
- The summary reports the actual total bytes processed. Verbose output also shows bytes per query.
- Madako runs the Overall profile before a STRING dimension and uses its distinct count without an additional query to decide whether to proceed.
- Different relations run concurrently up to `bigquery.threads`; queries for one relation run sequentially.
- A STRING dimension over `max_dimension_values` is skipped with a reason while other profiling continues.
- If a query or result validation fails, Madako does not save that run's results.
- Browsing or interacting with the Web UI never issues a BigQuery query.

## Supported profiles and current limitations

Madako supports STRING, INT64, FLOAT64, NUMERIC, BIGNUMERIC, BOOL, DATE, DATETIME, and TIMESTAMP columns.

It produces Overall profiles and profiles grouped by DATE, DATETIME, TIMESTAMP, or STRING dimensions. STRING profiles include distinct count and distinct ratio. A NULL bucket for a temporal dimension is shown separately from the time-series comparison. Each relation page shows one level of upstream and downstream lineage from direct dbt dependencies. Refresh reloads saved storage; it does not query BigQuery.

Current limitations:

- BigQuery is the only supported warehouse.
- Full dbt selection syntax is not implemented. `--select` accepts a model name or `unique_id`.
- Non-STRING categorical types cannot generate dimension queries.
- Storage targets local filesystems. Generation mode gives readers a stable completed generation during updates but does not support multiple writers.

Madako is in the 0.1 series. Public APIs and the storage format may change during the 0.x series.

## FAQ: How is Madako different from dbt-profiler?

[dbt-profiler](https://github.com/data-mie/dbt-profiler) is a dbt package whose macros can produce profiling SQL and an in-memory table, print a Markdown table, and generate schema YAML or docs blocks. Using its print macros alone does not add a dbt model. If you choose to persist a profile as a dbt model, that model adds a node to the dbt DAG.

Madako covers a different workflow: it selects configured dbt models and sources, runs their BigQuery profiles, saves the results separately as local Parquet files, and displays those results with dbt metadata in a Web UI. This does not add profiling models to the dbt DAG or persistent profile tables to BigQuery, and opening a saved result in the Web UI does not query BigQuery again.

## Python API

The same profiling flow is available from Python:

```python
from madako import DataProfile

catalog = DataProfile.from_dbt_project(
    project_dir="path/to/dbt-project",
    storage_dir=".madako",
    max_bytes_billed="1 GB",
)
plan = catalog.plan(select="events")
result = catalog.run(plan)

print(result.successful)
```

The Python API accepts the shared budget through `max_bytes_billed` on `DataProfile`, `from_storage`, or `from_dbt_project`. It does not automatically load `madako.toml`.

The distribution includes a PEP 561 `py.typed` marker, so type checkers such as mypy can use inline annotations from Madako's public API. Internal modules under `madako` include annotations but are not covered by compatibility guarantees during the 0.x series.

Warehouse and storage implementations can be replaced. See the [development status](../development-status.md) and [storage schema](../profile-storage-schema.md) for the current contracts.

## Development

```bash
git clone https://github.com/kohei0128/madako.git
cd madako
uv sync
uv run pytest
uv build
```

Node.js is needed only when changing the Web UI:

```bash
cd web
npm ci
npm run build
npm run test:e2e
```

`npm run test:e2e` prepares Chromium and the required shared libraries inside the project, under `web/.playwright-deps/`, without modifying Ubuntu system packages or requiring sudo. If port 8000 is busy, select another port, for example `MADAKO_E2E_PORT=18000 npm run test:e2e`.

See the [documentation index](../README.md) for technical contracts and the release procedure.
