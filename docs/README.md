# Madako documentation

The root [README](../README.md) is the concise English product introduction and the description rendered on PyPI. Use this page to find user guides, current technical references, and maintainer procedures.

## User guides

- [Getting started in English](en/getting-started.md)
- [日本語のスタートガイド](ja/getting-started.md)

The guides cover installation, the synthetic sample, dbt and BigQuery setup, configuration, query safety, current limitations, the Python API, and development checks.

## Technical reference

These documents describe the current implementation and compatibility contracts. They are currently maintained primarily in Japanese.

| Document | Purpose |
| --- | --- |
| [Development status](development-status.md) | Implemented behavior, known limitations, and verification coverage |
| [Profile storage schema](profile-storage-schema.md) | Parquet schema, compatibility, write guarantees, and recovery |
| [Config and storage versioning](config-versioning.md) | Versioning rules for configuration and persisted profiles |

## Maintainer guide

- [PyPI release procedure](releasing.md)

User-facing guidance belongs under `docs/en/` and `docs/ja/`. Technical references remain language-neutral by path and should describe the current code, not completed project plans or decision history; Git preserves that history.
