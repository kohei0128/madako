# Madako documentation

The root [README](../README.md) is the concise English product introduction and
the description rendered on PyPI. Detailed user guides are organized by
language below.

## User guides

- [Getting started in English](en/getting-started.md)
- [日本語のスタートガイド](ja/getting-started.md)

The two guides cover installation, the synthetic sample, dbt and BigQuery
setup, configuration, query safety, current limitations, the Python API, and
development checks.

## Technical and maintainer documents

The following documents are currently written primarily in Japanese. They are
kept at their existing paths so links remain stable while the public
documentation structure evolves.

| Document | Purpose |
| --- | --- |
| [Product requirements](product-requirements.md) | Original product goals, MVP scope, and detailed UX requirements |
| [Development status](development-status.md) | Implemented behavior, verification, and remaining decisions |
| [Roadmap](roadmap.md) | Completed phases and candidate follow-up work |
| [Profile storage schema](profile-storage-schema.md) | Parquet schema, compatibility, write guarantees, and recovery |
| [Config and storage versioning](config-versioning.md) | Versioning rules for configuration and persisted profiles |
| [Release procedure](releasing.md) | Maintainer procedure for TestPyPI and PyPI releases |

The technical documents are not yet maintained as fully translated English and
Japanese pairs. Translating all internal design history is outside the initial
documentation split; user-facing guidance should go under `docs/en/` and
`docs/ja/` from now on.
