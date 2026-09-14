# Madako

[![PyPI](https://img.shields.io/pypi/v/madako)](https://pypi.org/project/madako/)
[![Python](https://img.shields.io/pypi/pyversions/madako)](https://pypi.org/project/madako/)
[![CI](https://github.com/kohei0128/madako/actions/workflows/madako.yml/badge.svg)](https://github.com/kohei0128/madako/actions/workflows/madako.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/kohei0128/madako/blob/main/LICENSE)
[![OpenSSF Baseline](https://www.bestpractices.dev/projects/14631/baseline)](https://www.bestpractices.dev/projects/14631)

**Understand the data behind your dbt models.**

Madako profiles your dbt models in BigQuery and saves the results as local Parquet files. Explore row counts, missing values, and value ranges in the included web UI, alongside your dbt descriptions—and compare them across dates or categories.

![Madako showing dbt metadata and column profiles for a sample events model](https://raw.githubusercontent.com/kohei0128/madako/main/docs/assets/madako-overview.png)

Madako currently supports BigQuery and is experimental software in the 0.1 series.

## Try it in a minute

Madako requires Python 3.11 or later. The bundled sample uses synthetic data, so this path does not require dbt, BigQuery, or the `bq` CLI.

```bash
uv tool install madako
madako demo
```

Open <http://127.0.0.1:8000>. Madako uses temporary sample storage for the demo and removes it when the server stops. The local API documentation is available at <http://127.0.0.1:8000/docs>.

To install into an existing Python environment instead:

```bash
python -m pip install madako
```

## Use it with a dbt project

Profiling real data requires an installed and authenticated Google Cloud SDK `bq` CLI. Mark the models or sources you want to profile in dbt YAML:

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

Then generate dbt artifacts, profile the selected relations, and explore the results:

```bash
dbt docs generate
madako profile
madako serve
```

See the [English getting started guide](https://github.com/kohei0128/madako/blob/main/docs/en/getting-started.md) for configuration, query-safety behavior, supported profiles, and the Python API.

## Why Madako

- **Reusable profile results:** profiles are stored as local Parquet files, ready to query with your own tools. Browsing saved results requires no additional BigQuery queries.
- **Data with context:** view profiles alongside dbt descriptions, tests, and direct lineage to understand what each model and column represents.
- **Explicit query controls:** opt models into profiling and set per-relation maximum bytes billed in dbt YAML.

See the [profile storage schema](https://github.com/kohei0128/madako/blob/main/docs/profile-storage-schema.md) for the layout and contents of the saved Parquet files.

## Documentation

- [English getting started guide](https://github.com/kohei0128/madako/blob/main/docs/en/getting-started.md)
- [Japanese getting started guide](https://github.com/kohei0128/madako/blob/main/docs/ja/getting-started.md)
- [Documentation index](https://github.com/kohei0128/madako/blob/main/docs/README.md)

## Development

```bash
git clone https://github.com/kohei0128/madako.git
cd madako
uv sync
uv run pytest
uv build
```

See the [documentation index](https://github.com/kohei0128/madako/blob/main/docs/README.md) for Web UI checks, storage contracts, and release procedures.

## License

[MIT License](https://github.com/kohei0128/madako/blob/main/LICENSE)
