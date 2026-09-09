"""Warehouse boundary for query generation, execution, and result conversion."""

from dataclasses import dataclass
from typing import Protocol, cast

from data_profile.models import ModelProfile, ProfileSlice


@dataclass(frozen=True)
class QueryExecution:
    """Rows returned by a query and its warehouse-reported usage."""

    rows: list[dict]
    bytes_processed: int | None = None
    bytes_billed: int | None = None


class WarehouseAdapter(Protocol):
    @property
    def supported_types(self) -> frozenset[str]: ...

    def build_profile_query(self, model: ModelProfile, dimension: str | None) -> str: ...

    def estimate(self, sql: str, project: str, location: str) -> int: ...

    def execute(
        self, sql: str, project: str, location: str, max_bytes_billed: int | None,
    ) -> QueryExecution | list[dict]: ...

    def parse_profile_rows(
        self,
        model: ModelProfile,
        rows: list[dict],
        dimension: str | None,
    ) -> list[ProfileSlice]: ...


class BigQueryAdapter:
    """Uses the installed bq CLI and its existing authentication."""

    @property
    def supported_types(self) -> frozenset[str]:
        from data_profile import bigquery_profile

        return frozenset(bigquery_profile.SUPPORTED_TYPES)

    def build_profile_query(self, model: ModelProfile, dimension: str | None) -> str:
        from data_profile import bigquery_profile

        return bigquery_profile.generate_profile_sql(model, dimension)

    def estimate(self, sql: str, project: str, location: str) -> int:
        from data_profile import bigquery_profile

        return bigquery_profile.dry_run(sql, project, location)

    def execute(
        self, sql: str, project: str, location: str, max_bytes_billed: int | None,
    ) -> QueryExecution:
        from data_profile import bigquery_profile

        return bigquery_profile.execute_profile(sql, project, location, max_bytes_billed)

    def parse_profile_rows(
        self,
        model: ModelProfile,
        rows: list[dict],
        dimension: str | None,
    ) -> list[ProfileSlice]:
        from data_profile import bigquery_profile

        return bigquery_profile.rows_to_profiles(model, rows, dimension=dimension, validate=True)


class _LegacyBigQueryAdapter(BigQueryAdapter):
    """Adds BigQuery compilation to an older estimate/execute-only adapter."""

    def __init__(self, adapter: object) -> None:
        self._adapter = adapter

    def estimate(self, sql: str, project: str, location: str) -> int:
        return cast(WarehouseAdapter, self._adapter).estimate(sql, project, location)

    def execute(
        self, sql: str, project: str, location: str, max_bytes_billed: int | None,
    ) -> QueryExecution | list[dict]:
        return cast(WarehouseAdapter, self._adapter).execute(sql, project, location, max_bytes_billed)


def complete_adapter(adapter: object | None) -> WarehouseAdapter:
    """Return the complete contract while preserving the experimental 0.1 adapter shape."""
    if adapter is None:
        return BigQueryAdapter()
    required = ("supported_types", "build_profile_query", "parse_profile_rows")
    if all(hasattr(adapter, name) for name in required):
        return cast(WarehouseAdapter, adapter)
    return _LegacyBigQueryAdapter(adapter)
