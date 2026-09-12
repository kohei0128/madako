# Madako

[![PyPI](https://img.shields.io/pypi/v/madako)](https://pypi.org/project/madako/)
[![Python](https://img.shields.io/pypi/pyversions/madako)](https://pypi.org/project/madako/)
[![CI](https://github.com/kohei0128/madako/actions/workflows/madako.yml/badge.svg)](https://github.com/kohei0128/madako/actions/workflows/madako.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/kohei0128/madako/blob/main/LICENSE)

**See what your dbt models mean—and what their data actually looks like.**

Madako is a local-first data catalog that brings dbt metadata and profiles from
your warehouse into one browser UI. Browse descriptions, tests, lineage, row
counts, missing values, distinct values, ranges, and changes across dimensions
without issuing warehouse queries from the UI.

![Madako showing dbt metadata and column profiles for a sample events model](https://raw.githubusercontent.com/kohei0128/madako/main/docs/assets/madako-overview.png)

Madako currently supports BigQuery and is experimental software in the 0.1
series.

## Why Madako

- **Meaning and data together:** keep dbt descriptions, tests, and direct
  lineage next to profiles of the underlying data.
- **Changes in context:** compare missing values and type-specific metrics by
  DATE, DATETIME, TIMESTAMP, or low-cardinality STRING dimensions.
- **Explicit query controls:** opt models into profiling and set per-relation
  maximum bytes billed in dbt YAML.
- **Local-first browsing:** save results as local Parquet files and browse them
  without triggering new BigQuery queries.

```text
dbt artifacts + BigQuery -> Madako -> local Parquet storage -> Web UI
```

## Try it in a minute

Madako requires Python 3.11 or later. The bundled sample uses synthetic data,
so this path does not require dbt, BigQuery, or the `bq` CLI.

```bash
uv tool install madako
madako build-sample --output-dir .madako
madako serve --storage-dir .madako
```

Open <http://127.0.0.1:8000>. The local API documentation is available at
<http://127.0.0.1:8000/docs>.

To install into an existing Python environment instead:

```bash
python -m pip install madako
```

## Use it with a dbt project

Profiling real data requires an installed and authenticated Google Cloud SDK
`bq` CLI. Mark the models or sources you want to profile in dbt YAML:

```yaml
models:
  - name: events
    config:
      meta:
        profiling:
          enabled: true
          dimensions: [event_date, service]
          max_bytes_billed: "1 GB"
```

Then generate dbt artifacts, profile the selected relations, and open the
catalog:

```bash
dbt docs generate
madako profile
madako serve
```

See the [English getting started guide](https://github.com/kohei0128/madako/blob/main/docs/en/getting-started.md)
for configuration, query-safety behavior, supported profiles, and the Python
API.

## Documentation

- [English getting started guide](https://github.com/kohei0128/madako/blob/main/docs/en/getting-started.md)
- [Japanese getting started guide](https://github.com/kohei0128/madako/blob/main/docs/ja/getting-started.md)
- [Documentation index](https://github.com/kohei0128/madako/blob/main/docs/README.md)
- [Development status](https://github.com/kohei0128/madako/blob/main/docs/development-status.md)
- [Roadmap](https://github.com/kohei0128/madako/blob/main/docs/roadmap.md)

## Development

```bash
git clone https://github.com/kohei0128/madako.git
cd madako
uv sync
uv run pytest
uv build
```

See the [documentation index](https://github.com/kohei0128/madako/blob/main/docs/README.md)
for Web UI checks, storage contracts, and release procedures.

## License

[MIT License](https://github.com/kohei0128/madako/blob/main/LICENSE)
