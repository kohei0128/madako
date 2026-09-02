import json
import subprocess
from datetime import UTC, datetime

from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice


SUPPORTED_TYPES = {"STRING", "INT64", "FLOAT64", "BOOL", "DATE"}


class ProfilingError(Exception):
    pass


def generate_profile_sql(model: ModelProfile, dimension: str | None = None) -> str:
    supported = [column for column in model.columns if column.data_type in SUPPORTED_TYPES]
    if not supported:
        raise ProfilingError(f"no supported columns found for {model.name}")
    if dimension is None:
        overall_metrics = _aggregate_expressions(supported)
        overall_structs = _metric_structs(supported)
        relation = model.relation_name or f"`{model.database}.{model.schema_name}.{model.name}`"
        return f"""
WITH overall_agg AS (
  SELECT
    COUNT(*) AS record_count,
    {overall_metrics}
  FROM {relation}
)
SELECT
  CAST(NULL AS STRING) AS dimension_name,
  CAST(NULL AS STRING) AS dimension_value,
  record_count,
  metric.*
FROM overall_agg
CROSS JOIN UNNEST([{overall_structs}]) AS metric
ORDER BY column_order
""".strip()

    dimension_column = next((column for column in model.columns if column.name == dimension), None)
    if dimension_column is None:
        raise ProfilingError(f"dimension column not found: {dimension}")
    if dimension_column.data_type != "DATE":
        raise ProfilingError(f"pilot dimension must be DATE, got {dimension_column.data_type}")

    overall_metrics = _aggregate_expressions(supported)
    dimension_metrics = _aggregate_expressions(supported)
    overall_structs = _metric_structs(supported)
    dimension_structs = _metric_structs(supported)
    relation = model.relation_name or f"`{model.database}.{model.schema_name}.{model.name}`"
    return f"""
WITH overall_agg AS (
  SELECT
    COUNT(*) AS record_count,
    {overall_metrics}
  FROM {relation}
),
dimension_agg AS (
  SELECT
    CAST(`{dimension}` AS STRING) AS dimension_value,
    COUNT(*) AS record_count,
    {dimension_metrics}
  FROM {relation}
  GROUP BY `{dimension}`
),
overall_profile AS (
  SELECT
    CAST(NULL AS STRING) AS dimension_name,
    CAST(NULL AS STRING) AS dimension_value,
    record_count,
    metric.*
  FROM overall_agg
  CROSS JOIN UNNEST([{overall_structs}]) AS metric
),
dimension_profile AS (
  SELECT
    '{_escape_string(dimension)}' AS dimension_name,
    dimension_value,
    record_count,
    metric.*
  FROM dimension_agg
  CROSS JOIN UNNEST([{dimension_structs}]) AS metric
)
SELECT * FROM overall_profile
UNION ALL
SELECT * FROM dimension_profile
ORDER BY dimension_name, dimension_value, column_order
""".strip()


def dry_run(sql: str, project: str, location: str) -> int:
    payload = _run_bq([
        "bq", "query", f"--project_id={project}", f"--location={location}",
        "--use_legacy_sql=false", "--dry_run", "--format=json", sql,
    ])
    return int(payload.get("statistics", {}).get("totalBytesProcessed", 0))


def execute_profile(sql: str, project: str, location: str, max_bytes_billed: int) -> list[dict]:
    payload = _run_bq([
        "bq", "query", f"--project_id={project}", f"--location={location}",
        "--use_legacy_sql=false", f"--maximum_bytes_billed={max_bytes_billed}",
        "--format=json", "--max_rows=100000", sql,
    ])
    if not isinstance(payload, list):
        raise ProfilingError("unexpected BigQuery result")
    return payload


def rows_to_profiles(model: ModelProfile, rows: list[dict]) -> list[ProfileSlice]:
    columns = {column.name: column for column in model.columns}
    grouped: dict[tuple[str | None, str | None], list[dict]] = {}
    for row in rows:
        key = (row.get("dimension_name"), row.get("dimension_value"))
        grouped.setdefault(key, []).append(row)

    profiles: list[ProfileSlice] = []
    for (dimension_name, dimension_value), metric_rows in grouped.items():
        metric_rows.sort(key=lambda row: int(row["column_order"]))
        profile_columns = [_row_to_column(row, columns[row["column_name"]]) for row in metric_rows]
        profiles.append(ProfileSlice(
            dimension_name=dimension_name,
            dimension_value=dimension_value,
            record_count=int(metric_rows[0]["record_count"]),
            columns=profile_columns,
        ))
    profiles.sort(key=lambda profile: (profile.dimension_name is not None, profile.dimension_name or "", profile.dimension_value or ""))
    return profiles


def apply_profile(model: ModelProfile, rows: list[dict]) -> ModelProfile:
    return model.model_copy(update={"profiles": rows_to_profiles(model, rows), "profiled_at": datetime.now(UTC)})


def _aggregate_expressions(columns: list[ColumnMetadata]) -> str:
    expressions: list[str] = []
    for index, column in enumerate(columns):
        quoted = f"`{column.name}`"
        expressions.append(f"COUNTIF({quoted} IS NULL) AS m{index}_null_count")
        expressions.append(
            f"COUNT(DISTINCT {quoted}) AS m{index}_distinct_count"
            if column.data_type == "STRING" else f"CAST(NULL AS INT64) AS m{index}_distinct_count"
        )
        expressions.append(
            f"CAST(MIN({quoted}) AS STRING) AS m{index}_min_value"
            if column.data_type in {"INT64", "FLOAT64", "DATE"} else f"CAST(NULL AS STRING) AS m{index}_min_value"
        )
        expressions.append(
            f"CAST(MAX({quoted}) AS STRING) AS m{index}_max_value"
            if column.data_type in {"INT64", "FLOAT64", "DATE"} else f"CAST(NULL AS STRING) AS m{index}_max_value"
        )
        expressions.append(
            f"COUNTIF({quoted} IS TRUE) AS m{index}_true_count"
            if column.data_type == "BOOL" else f"CAST(NULL AS INT64) AS m{index}_true_count"
        )
    return ",\n    ".join(expressions)


def _metric_structs(columns: list[ColumnMetadata]) -> str:
    structs = []
    for index, column in enumerate(columns):
        structs.append(f"""STRUCT(
      {index} AS column_order,
      '{_escape_string(column.name)}' AS column_name,
      '{column.data_type}' AS column_type,
      m{index}_null_count AS null_count,
      SAFE_DIVIDE(m{index}_null_count, record_count) AS null_rate,
      m{index}_distinct_count AS distinct_count,
      m{index}_min_value AS min_value,
      m{index}_max_value AS max_value,
      m{index}_true_count AS true_count
    )""")
    return ",\n    ".join(structs)


def _row_to_column(row: dict, metadata: ColumnMetadata) -> ColumnProfile:
    data_type = metadata.data_type
    min_value = row.get("min_value")
    max_value = row.get("max_value")
    if data_type == "INT64":
        min_value = int(min_value) if min_value is not None else None
        max_value = int(max_value) if max_value is not None else None
    elif data_type == "FLOAT64":
        min_value = float(min_value) if min_value is not None else None
        max_value = float(max_value) if max_value is not None else None
    return ColumnProfile(
        name=metadata.name,
        data_type=data_type,
        description=metadata.description,
        null_count=int(row["null_count"]),
        null_rate=float(row.get("null_rate") or 0),
        distinct_count=int(row["distinct_count"]) if row.get("distinct_count") is not None else None,
        min_value=min_value,
        max_value=max_value,
        true_count=int(row["true_count"]) if row.get("true_count") is not None else None,
    )


def _run_bq(command: list[str]) -> dict | list:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise ProfilingError("bq CLI was not found") from error
    except subprocess.CalledProcessError as error:
        raise ProfilingError(error.stderr.strip() or "BigQuery command failed") from error
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ProfilingError("could not parse bq output") from error


def _escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
