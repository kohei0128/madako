# Madako

[![PyPI](https://img.shields.io/pypi/v/madako)](https://pypi.org/project/madako/)
[![Python](https://img.shields.io/pypi/pyversions/madako)](https://pypi.org/project/madako/)
[![CI](https://github.com/kohei0128/madako/actions/workflows/madako.yml/badge.svg)](https://github.com/kohei0128/madako/actions/workflows/madako.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/kohei0128/madako/blob/main/LICENSE)
[![OpenSSF Baseline](https://www.bestpractices.dev/projects/14631/baseline)](https://www.bestpractices.dev/projects/14631)

**Understand the data behind your dbt models.**

Madako brings your dbt descriptions and data profiles together in a local web UI. Profile your BigQuery data, explore missing values and value ranges across dates or categories, and revisit saved results without querying BigQuery again.

Profiles are stored separately as local Parquet files, so you do not need to add profiling models or persistent BigQuery tables. Your existing dbt DAG stays focused on its analytical models.

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

- **Data with context:** inspect profiles alongside dbt descriptions, tests, and direct lineage, with date or category breakdowns when you need them.
- **A focused dbt DAG:** keep profile results in local Parquet files instead of adding profiling models or persistent BigQuery tables. Reopening saved results requires no BigQuery query.
- **Explicit query controls:** opt models into profiling and set per-relation maximum bytes billed in dbt YAML.

See the [profile storage schema](https://github.com/kohei0128/madako/blob/main/docs/profile-storage-schema.md) for the layout and contents of the saved Parquet files.

## Documentation

- [English getting started guide](https://github.com/kohei0128/madako/blob/main/docs/en/getting-started.md)
- [Japanese getting started guide](https://github.com/kohei0128/madako/blob/main/docs/ja/getting-started.md)
- [Documentation index](https://github.com/kohei0128/madako/blob/main/docs/README.md)

Found a bug or have an idea? [Open an issue](https://github.com/kohei0128/madako/issues/new/choose). For security vulnerabilities, please follow our [Security Policy](https://github.com/kohei0128/madako/security/policy).

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
