import json
import subprocess
from math import isclose

from data_profile.exceptions import ProfilingError, ResultValidationError, WarehouseError
from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice


MAX_RESULT_ROWS = 100_000


TEMPORAL_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
SUPPORTED_TYPES = {"STRING", "INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC", "BOOL", *TEMPORAL_TYPES}


def generate_profile_sql(model: ModelProfile, dimension: str | None = None) -> str:
    supported = [column for column in model.columns if column.data_type in SUPPORTED_TYPES]
    if not supported:
        raise ResultValidationError(f"no supported columns found for {model.name}")
    if dimension is None:
        overall_metrics = _aggregate_expressions(supported, model.profiling.treat_empty_string_as_null)
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
        raise ResultValidationError(f"dimension column not found: {dimension}")
    if dimension_column.data_type not in {*TEMPORAL_TYPES, "STRING"}:
        raise ResultValidationError(
            "dimension must be DATE, DATETIME, TIMESTAMP or STRING, "
            f"got {dimension_column.data_type}"
        )

    dimension_metrics = _aggregate_expressions(supported, model.profiling.treat_empty_string_as_null)
    dimension_structs = _metric_structs(supported)
    relation = model.relation_name or f"`{model.database}.{model.schema_name}.{model.name}`"
    return f"""
WITH dimension_agg AS (
  SELECT
    CAST(`{dimension}` AS STRING) AS dimension_value,
    COUNT(*) AS record_count,
    {dimension_metrics}
  FROM {relation}
  GROUP BY `{dimension}`
)
SELECT
  '{_escape_string(dimension)}' AS dimension_name,
  dimension_value,
  record_count,
  metric.*
FROM dimension_agg
CROSS JOIN UNNEST([{dimension_structs}]) AS metric
ORDER BY dimension_value, column_order
""".strip()


def dry_run(sql: str, project: str, location: str) -> int:
    payload = _run_bq([
        "bq", "query", f"--project_id={project}", f"--location={location}",
        "--use_legacy_sql=false", "--dry_run", "--format=json", sql,
    ])
    try:
        estimate = int(payload["statistics"]["totalBytesProcessed"])
    except (KeyError, TypeError, ValueError) as error:
        raise WarehouseError("dry run did not return a valid byte estimate") from error
    if estimate < 0:
        raise WarehouseError("dry run returned a negative byte estimate")
    return estimate


def execute_profile(sql: str, project: str, location: str, max_bytes_billed: int) -> list[dict]:
    payload = _run_bq([
        "bq", "query", f"--project_id={project}", f"--location={location}",
        "--use_legacy_sql=false", f"--maximum_bytes_billed={max_bytes_billed}",
        "--format=json", f"--max_rows={MAX_RESULT_ROWS}", sql,
    ])
    if not isinstance(payload, list):
        raise WarehouseError("unexpected BigQuery result")
    if len(payload) >= MAX_RESULT_ROWS:
        raise ResultValidationError("query result reached the row limit; refusing potentially truncated metrics")
    return payload


def rows_to_profiles(
    model: ModelProfile, rows: list[dict], *, dimension: str | None = None, validate: bool = False,
) -> list[ProfileSlice]:
    columns = {column.name: column for column in model.columns}
    grouped: dict[tuple[str | None, str | None], list[dict]] = {}
    for row in rows:
        key = (row.get("dimension_name"), row.get("dimension_value"))
        grouped.setdefault(key, []).append(row)

    profiles: list[ProfileSlice] = []
    for (dimension_name, dimension_value), metric_rows in grouped.items():
        metric_rows.sort(key=lambda row: int(row["column_order"]))
        if validate:
            expected = {column.name: column.data_type for column in model.columns if column.data_type in SUPPORTED_TYPES}
            actual = {row["column_name"]: row["column_type"] for row in metric_rows}
            if actual != expected or len(metric_rows) != len(expected):
                raise ResultValidationError("query result has missing, duplicate or mismatched columns")
            if len({int(row["record_count"]) for row in metric_rows}) != 1:
                raise ResultValidationError("query result has inconsistent record counts")
        profile_columns = [_row_to_column(row, columns[row["column_name"]]) for row in metric_rows]
        profiles.append(ProfileSlice(
            dimension_name=dimension_name,
            dimension_value=dimension_value,
            record_count=int(metric_rows[0]["record_count"]),
            columns=profile_columns,
        ))
    profiles.sort(key=lambda profile: (profile.dimension_name is not None, profile.dimension_name or "", profile.dimension_value or ""))
    if validate:
        _validate_profiles(model, profiles, dimension)
    return profiles


def _validate_profiles(model: ModelProfile, profiles: list[ProfileSlice], dimension: str | None) -> None:
    overall = [profile for profile in profiles if profile.dimension_name is None]
    buckets = [profile for profile in profiles if profile.dimension_name is not None]
    if dimension is None:
        if len(overall) != 1 or buckets:
            raise ResultValidationError("query result must contain exactly one Overall profile")
    elif overall or any(profile.dimension_name != dimension for profile in buckets):
        raise ResultValidationError("query result contains an unexpected dimension")
    for profile in profiles:
        for column in profile.columns:
            expected_missing = column.null_count + (
                column.empty_string_count
                if model.profiling.treat_empty_string_as_null and column.data_type == "STRING" else 0
            )
            if (column.missing_count != expected_missing
                    or column.null_count + column.empty_string_count > profile.record_count
                    or column.null_count + (column.true_count or 0) > profile.record_count
                    or (column.distinct_count or 0) > profile.record_count - column.missing_count):
                raise ResultValidationError("query result has inconsistent metric counts")
            for count, rate in [(column.null_count, column.null_rate), (column.missing_count, column.missing_rate)]:
                expected_rate = count / profile.record_count if profile.record_count else 0
                if not isclose(rate, expected_rate, rel_tol=1e-6, abs_tol=1e-9):
                    raise ResultValidationError("query result has inconsistent metric rates")
            if column.distinct_count is not None:
                expected_ratio = column.distinct_count / profile.record_count if profile.record_count else 0
                if column.distinct_ratio is None or not isclose(
                    column.distinct_ratio, expected_ratio, rel_tol=1e-6, abs_tol=1e-9,
                ):
                    raise ResultValidationError("query result has an inconsistent distinct ratio")


def _aggregate_expressions(columns: list[ColumnMetadata], treat_empty_string_as_null: bool) -> str:
    expressions: list[str] = []
    for index, column in enumerate(columns):
        quoted = f"`{column.name}`"
        expressions.append(f"COUNTIF({quoted} IS NULL) AS m{index}_null_count")
        expressions.append(
            f"COUNTIF({quoted} = '') AS m{index}_empty_string_count"
            if column.data_type == "STRING" else f"0 AS m{index}_empty_string_count"
        )
        missing_condition = (
            f"{quoted} IS NULL OR {quoted} = ''"
            if column.data_type == "STRING" and treat_empty_string_as_null
            else f"{quoted} IS NULL"
        )
        expressions.append(f"COUNTIF({missing_condition}) AS m{index}_missing_count")
        expressions.append(
            f"COUNT(DISTINCT NULLIF({quoted}, '')) AS m{index}_distinct_count"
            if column.data_type == "STRING" and treat_empty_string_as_null
            else f"COUNT(DISTINCT {quoted}) AS m{index}_distinct_count"
            if column.data_type == "STRING" else f"CAST(NULL AS INT64) AS m{index}_distinct_count"
        )
        expressions.append(
            f"CAST(MIN({quoted}) AS STRING) AS m{index}_min_value"
            if column.data_type in {"INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC", *TEMPORAL_TYPES}
            else f"CAST(NULL AS STRING) AS m{index}_min_value"
        )
        expressions.append(
            f"CAST(MAX({quoted}) AS STRING) AS m{index}_max_value"
            if column.data_type in {"INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC", *TEMPORAL_TYPES}
            else f"CAST(NULL AS STRING) AS m{index}_max_value"
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
      m{index}_empty_string_count AS empty_string_count,
      m{index}_missing_count AS missing_count,
      SAFE_DIVIDE(m{index}_missing_count, record_count) AS missing_rate,
      m{index}_distinct_count AS distinct_count,
      SAFE_DIVIDE(m{index}_distinct_count, record_count) AS distinct_ratio,
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
    distinct_count = int(row["distinct_count"]) if row.get("distinct_count") is not None else None
    record_count = int(row["record_count"])
    if row.get("distinct_ratio") is not None:
        distinct_ratio = float(row["distinct_ratio"])
    elif distinct_count is None:
        distinct_ratio = None
    else:
        distinct_ratio = distinct_count / record_count if record_count else 0
    return ColumnProfile(
        name=metadata.name,
        data_type=data_type,
        description=metadata.description,
        null_count=int(row["null_count"]),
        null_rate=float(row.get("null_rate") or 0),
        empty_string_count=int(row.get("empty_string_count") or 0),
        missing_count=int(row.get("missing_count", row["null_count"])),
        missing_rate=float(row.get("missing_rate", row.get("null_rate")) or 0),
        distinct_count=distinct_count,
        distinct_ratio=distinct_ratio,
        min_value=min_value,
        max_value=max_value,
        true_count=int(row["true_count"]) if row.get("true_count") is not None else None,
    )


def _run_bq(command: list[str]) -> dict | list:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise WarehouseError("bq CLI was not found") from error
    except subprocess.CalledProcessError as error:
        raise WarehouseError(error.stderr.strip() or "BigQuery command failed") from error
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WarehouseError("could not parse bq output") from error


def _escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
