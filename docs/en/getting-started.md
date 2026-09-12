# Getting started with Madako

[日本語版](../ja/getting-started.md) · [Documentation index](../README.md) ·
[Project README](../../README.md)

Madako is a local-first data catalog that displays dbt metadata and profiles of
the underlying data in one browser UI. It currently supports BigQuery and is
experimental software in the 0.1 series.

## Requirements and installation

Madako requires Python 3.11 or later. Install the CLI, Python API, and Web UI in
an isolated environment with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install madako
```

To use an existing Python environment instead:

```bash
python -m pip install madako
```

This package includes the Python API, CLI, and prebuilt Web UI. Profiling real
BigQuery data additionally requires the Google Cloud SDK `bq` CLI to be
installed and authenticated.

## Try the Web UI with sample data

The bundled sample uses synthetic data and does not require dbt, BigQuery, or
the `bq` CLI.

```bash
madako build-sample --output-dir .madako
madako serve --storage-dir .madako
```

Open <http://127.0.0.1:8000>. The local API documentation is available at
<http://127.0.0.1:8000/docs>.

## Use Madako with your dbt project

### 1. Choose what to profile

Add `meta.profiling` to the YAML entry for each model or source you want to
profile:

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

After changing profiling configuration, refresh the dbt artifacts with
`dbt docs generate` or `dbt parse` before running `madako profile` again.

If `dimensions` is omitted, Madako only calculates an overall profile. A
dimension may be a DATE, DATETIME, TIMESTAMP, or STRING column. Madako skips an
individual STRING dimension when its overall distinct count exceeds
`max_dimension_values`, which defaults to 10,000. The distinct ratio is shown
in the UI but does not control whether the dimension runs.

With `treat_empty_string_as_null: true`, empty strings (`''`) in STRING columns
are included in the Missing metric alongside NULL values. The default is
`false`, which counts only NULL as Missing. Strings containing only whitespace,
such as `' '`, are not empty strings.

Set `max_bytes_billed` on any model or source for which you want a preflight
query-cost check. Madako dry-runs every query for that relation and starts no
real query for it if any estimate exceeds the limit. Relations without this
setting skip the dry run.

```yaml
config:
  meta:
    profiling:
      enabled: true
      max_bytes_billed: "1 GB"
```

`max_bytes_billed` also accepts an integer number of bytes. Human-readable
values may use `KB`, `MB`, `GB`, and `TB` for decimal units or `KiB`, `MiB`,
`GiB`, and `TiB` for binary units.

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

[server]
host = "127.0.0.1"
port = 8000

[storage]
mode = "generations"
keep_generations = 3
```

Relative paths are resolved from the directory containing `madako.toml`.
Madako searches for this file from the current directory upward, so commands
can run from anywhere inside the project. Use a different file with
`madako --config path/to/madako.toml <command>`.

`bigquery.threads` is the maximum number of relations profiled concurrently
and defaults to 4. Different relations can run in parallel. Within one
relation, Madako completes the Overall query before running dimension queries
sequentially. Override the value for one run with `madako profile --threads 8`.

With `storage.mode = "generations"`, Madako writes and validates a complete new
generation before atomically switching the `current` symlink. The current
generation is included in `keep_generations`, which defaults to 3 and must be
at least 2. This mode assumes a local filesystem and a single writer; it does
not guarantee writes from multiple processes. If `[storage]` is omitted,
Madako uses direct mode. `DATA_PROFILE_USE_GENERATIONS=1` remains available for
compatibility, but new configurations should use `madako.toml`.

Generation mode can read an existing direct-mode directory. The next
successful save creates the generation layout; until then Madako continues to
read the two existing Parquet files.

Add `.madako/` to the dbt project's `.gitignore` if generated storage should
not be committed.

### 3. Generate artifacts, profile, and browse

Generate dbt artifacts before running Madako:

```bash
dbt docs generate
madako profile
madako serve
```

For a development loop that only changes YAML descriptions, tests, or
`meta.profiling`, `dbt parse` is faster:

```bash
dbt parse
madako profile
```

`dbt parse` updates only `manifest.json`; it does not refresh the column types
or ordering that `dbt docs generate` writes to `catalog.json`. If an existing
catalog is stale, Madako warns and uses it. Without a catalog, Madako uses YAML
`data_type` values. Columns without type information in either place become
`UNKNOWN` and are excluded from profiling. Run `dbt docs generate` after
changing physical columns, on the first import, or whenever accurate warehouse
types are required.

`madako profile` imports `manifest.json` and `catalog.json` into storage before
profiling BigQuery. It does not run a dbt command for you.

By default, the terminal shows Import, Plan, Profile, and Save progress plus a
summary. Interactive terminals update progress in place; redirected output and
CI append lines without ANSI cursor control. Use `madako profile --verbose` (or
`-v`) for dry runs, query starts and completions, and bytes processed per
query. Failures and dimensions skipped for cardinality are visible without
verbose mode; verbose mode also explains queries not run after a failure. The
summary counts successful queries separately from skipped queries. `TERM=dumb`
disables line rewriting and color, so `NO_COLOR` does not otherwise change the
content.

## Command overview

| Command | Purpose |
| --- | --- |
| `madako profile` | Import dbt artifacts, profile enabled relations, and save the results |
| `madako serve` | Open a saved catalog in the local browser UI |
| `madako import-dbt` | Refresh dbt metadata without connecting to BigQuery |
| `madako plan --show-sql` | Inspect estimates and generated SQL from saved metadata |
| `madako build-sample` | Create synthetic storage for trying the UI |

Command-line options can temporarily override configuration. Discover them
with:

```bash
madako --help
madako profile --help
```

## Query safety

- Madako dry-runs queries only for relations that set `max_bytes_billed`.
- If any dry-run estimate for a relation exceeds its limit, Madako starts no
  real query for that relation.
- The summary reports the total bytes processed by BigQuery. Verbose output
  also shows bytes per query.
- Madako runs the Overall profile before a STRING dimension and uses its
  distinct count without an additional query to decide whether to proceed.
- Different relations run concurrently up to `bigquery.threads`; queries for
  one relation run sequentially.
- A STRING dimension over `max_dimension_values` is skipped with a reason while
  other profiling continues.
- If a query or result validation fails, Madako does not save that run's
  results.
- Browsing or interacting with the Web UI never issues a BigQuery query.

## Supported profiles and current limitations

Madako supports STRING, INT64, FLOAT64, NUMERIC, BIGNUMERIC, BOOL, DATE,
DATETIME, and TIMESTAMP columns.

It produces Overall profiles and profiles grouped by DATE, DATETIME, TIMESTAMP,
or STRING dimensions. STRING profiles include distinct count and distinct
ratio. A NULL bucket for a temporal dimension is shown separately from the
time-series comparison. Each relation page shows one level of upstream and
downstream lineage from direct dbt dependencies. Refresh reloads saved storage;
it does not query BigQuery.

Current limitations:

- BigQuery is the only supported warehouse.
- Full dbt selection syntax is not implemented. `--select` accepts a model name
  or `unique_id`.
- Non-STRING categorical types cannot generate dimension queries.
- Storage targets local filesystems. Generation mode gives readers a stable
  completed generation during updates but does not support multiple writers.

Madako is in the 0.1 series. Public APIs and the storage format may change
during the 0.x series.

## Python API

The same profiling flow is available from Python:

```python
from madako import DataProfile

catalog = DataProfile.from_dbt_project(
    project_dir="path/to/dbt-project",
    storage_dir=".madako",
)
plan = catalog.plan(select="events")
result = catalog.run(plan)

print(result.successful)
```

The distribution includes a PEP 561 `py.typed` marker, so type checkers such as
mypy can use inline annotations from Madako's public API. Internal modules under
`madako` include annotations but are not covered by compatibility guarantees
during the 0.x series.

Warehouse and storage implementations can be replaced. See the
[development status](../development-status.md) and
[storage schema](../profile-storage-schema.md) for the current contracts.

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

`npm run test:e2e` prepares Chromium and the required shared libraries inside
the project, under `web/.playwright-deps/`, without modifying Ubuntu system
packages or requiring sudo. If port 8000 is busy, select another port, for
example `MADAKO_E2E_PORT=18000 npm run test:e2e`.

See the [documentation index](../README.md) for technical contracts and the
release procedure.
