"""Warehouse execution boundary. SQL generation currently targets BigQuery."""

from typing import Protocol

from data_profile import bigquery_profile


class WarehouseAdapter(Protocol):
    def estimate(self, sql: str, project: str, location: str) -> int: ...

    def execute(self, sql: str, project: str, location: str, max_bytes_billed: int) -> list[dict]: ...


class BigQueryAdapter:
    """Uses the installed bq CLI and its existing authentication."""

    def estimate(self, sql: str, project: str, location: str) -> int:
        return bigquery_profile.dry_run(sql, project, location)

    def execute(self, sql: str, project: str, location: str, max_bytes_billed: int) -> list[dict]:
        return bigquery_profile.execute_profile(sql, project, location, max_bytes_billed)
