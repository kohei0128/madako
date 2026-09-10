import subprocess

from data_profile.bigquery_profile import ProfilingError, generate_profile_sql, rows_to_profiles
from data_profile.models import ColumnMetadata, ModelProfile, ProfilingConfig


def model() -> ModelProfile:
    return ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        relation_name="`project.dataset.events`",
        materialization="table",
        columns=[
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="category", data_type="STRING"),
            ColumnMetadata(name="amount", data_type="INT64"),
        ],
    )


def test_generates_dimension_only_sql() -> None:
    sql = generate_profile_sql(model(), "event_date")

    assert "FROM `project.dataset.events`" in sql
    assert "GROUP BY `event_date`" in sql
    assert "COUNT(DISTINCT `category`)" in sql
    assert "CAST(MIN(`amount`) AS STRING)" in sql
    assert "overall_agg" not in sql
    assert "UNION ALL" not in sql


def test_string_can_be_used_as_dimension() -> None:
    sql = generate_profile_sql(model(), "category")

    assert "GROUP BY `category`" in sql
    assert "'category' AS dimension_name" in sql


def test_non_temporal_or_string_dimension_is_rejected() -> None:
    import pytest

    with pytest.raises(ProfilingError, match="DATE, DATETIME, TIMESTAMP or STRING"):
        generate_profile_sql(model(), "amount")


def test_datetime_and_timestamp_generate_metrics_and_can_be_dimensions() -> None:
    temporal_model = model().model_copy(update={"columns": [
        ColumnMetadata(name="created_at", data_type="DATETIME"),
        ColumnMetadata(name="received_at", data_type="TIMESTAMP"),
    ]})

    overall_sql = generate_profile_sql(temporal_model)

    assert "CAST(MIN(`created_at`) AS STRING)" in overall_sql
    assert "CAST(MAX(`created_at`) AS STRING)" in overall_sql
    assert "CAST(MIN(`received_at`) AS STRING)" in overall_sql
    assert "CAST(MAX(`received_at`) AS STRING)" in overall_sql
    for dimension in ("created_at", "received_at"):
        dimension_sql = generate_profile_sql(temporal_model, dimension)
        assert f"CAST(`{dimension}` AS STRING) AS dimension_value" in dimension_sql
        assert f"GROUP BY `{dimension}`" in dimension_sql

    values = ["2026-09-09 12:34:56.123456", "2026-09-09 03:34:56.123456+00"]
    rows = [{
        "dimension_name": None,
        "dimension_value": None,
        "record_count": "1",
        "column_order": str(order),
        "column_name": column.name,
        "column_type": column.data_type,
        "null_count": "0",
        "null_rate": "0",
        "distinct_count": None,
        "min_value": values[order],
        "max_value": values[order],
        "true_count": None,
    } for order, column in enumerate(temporal_model.columns)]

    columns = rows_to_profiles(temporal_model, rows, validate=True)[0].columns
    assert [(column.data_type, column.min_value) for column in columns] == [
        ("DATETIME", values[0]),
        ("TIMESTAMP", values[1]),
    ]


def test_reconstructs_profile_slices() -> None:
    rows = [
        {
            "dimension_name": None, "dimension_value": None, "record_count": "10",
            "column_order": "2", "column_name": "amount", "column_type": "INT64",
            "null_count": "1", "null_rate": "0.1", "distinct_count": None,
            "min_value": "2", "max_value": "50", "true_count": None,
        },
        {
            "dimension_name": "event_date", "dimension_value": "2026-09-01", "record_count": "4",
            "column_order": "2", "column_name": "amount", "column_type": "INT64",
            "null_count": "0", "null_rate": "0", "distinct_count": None,
            "min_value": "4", "max_value": "30", "true_count": None,
        },
    ]

    profiles = rows_to_profiles(model(), rows)

    assert len(profiles) == 2
    assert profiles[0].dimension_name is None
    assert profiles[0].columns[0].min_value == 2
    assert profiles[1].dimension_value == "2026-09-01"


def test_generates_overall_only_sql_without_dimension() -> None:
    sql = generate_profile_sql(model())

    assert "overall_agg" in sql
    assert "dimension_agg" not in sql
    assert "CAST(NULL AS STRING) AS dimension_name" in sql


def test_empty_strings_can_be_counted_as_missing_and_excluded_from_distinct() -> None:
    configured = model().model_copy(update={
        "profiling": ProfilingConfig(treat_empty_string_as_null=True),
    })

    sql = generate_profile_sql(configured)

    assert "COUNTIF(`category` = '') AS m1_empty_string_count" in sql
    assert "COUNTIF(`category` IS NULL OR `category` = '') AS m1_missing_count" in sql
    assert "COUNT(DISTINCT NULLIF(`category`, ''))" in sql
    assert "SAFE_DIVIDE(m1_distinct_count, record_count) AS distinct_ratio" in sql
    assert "COUNTIF(`amount` IS NULL) AS m2_missing_count" in sql


def test_reconstructs_separate_null_empty_and_missing_metrics() -> None:
    rows = [{
        "dimension_name": None,
        "dimension_value": None,
        "record_count": "10",
        "column_order": "1",
        "column_name": "category",
        "column_type": "STRING",
        "null_count": "1",
        "null_rate": "0.1",
        "empty_string_count": "2",
        "missing_count": "3",
        "missing_rate": "0.3",
        "distinct_count": "4",
        "min_value": None,
        "max_value": None,
        "true_count": None,
    }]

    column = rows_to_profiles(model(), rows)[0].columns[0]

    assert column.null_count == 1
    assert column.empty_string_count == 2
    assert column.missing_count == 3
    assert column.missing_rate == 0.3
    assert column.distinct_count == 4
    assert column.distinct_ratio == 0.4


def test_bq_result_limit_is_not_silently_saved(monkeypatch) -> None:
    import pytest
    from data_profile import bigquery_profile as bq
    monkeypatch.setattr(bq, "MAX_RESULT_ROWS", 2)
    monkeypatch.setattr(bq, "_run_bq", lambda *_, **__: [{}, {}])
    with pytest.raises(bq.ProfilingError, match="row limit"):
        bq.execute_profile("sql", "project", "US", 1000)


def test_execute_profile_returns_job_byte_usage(monkeypatch) -> None:
    from data_profile import bigquery_profile as bq

    calls: list[tuple[list[str], str | None]] = []
    responses = iter([
        [{"record_count": "1"}],
        {"statistics": {"query": {
            "totalBytesProcessed": "123456",
            "totalBytesBilled": "10485760",
        }}},
    ])

    def run(command: list[str], *, input_text: str | None = None):
        calls.append((command, input_text))
        return next(responses)

    monkeypatch.setattr(bq, "_run_bq", run)
    result = bq.execute_profile("SELECT 1", "project", "US", None)

    assert result.rows == [{"record_count": "1"}]
    assert result.bytes_processed == 123_456
    assert result.bytes_billed == 10_485_760
    query_command, query_input = calls[0]
    show_command, show_input = calls[1]
    assert query_command[0] == "bq"
    assert query_command[1].startswith("--job_id=madako_")
    assert "SELECT 1" not in query_command
    assert query_input == "SELECT 1"
    assert not any(arg.startswith("--maximum_bytes_billed=") for arg in query_command)
    assert show_command[-1] == query_command[1].removeprefix("--job_id=")
    assert show_input is None


def test_execute_profile_applies_optional_maximum_bytes(monkeypatch) -> None:
    from data_profile import bigquery_profile as bq

    calls: list[tuple[list[str], str | None]] = []
    responses = iter([
        [],
        {"statistics": {"query": {
            "totalBytesProcessed": "0",
            "totalBytesBilled": "0",
        }}},
    ])
    monkeypatch.setattr(
        bq,
        "_run_bq",
        lambda command, *, input_text=None: calls.append((command, input_text)) or next(responses),
    )

    bq.execute_profile("SELECT 1", "project", "US", 1_000_000)

    assert "--maximum_bytes_billed=1000000" in calls[0][0]
    assert calls[0][1] == "SELECT 1"


def test_dry_run_passes_query_through_standard_input(monkeypatch) -> None:
    from data_profile import bigquery_profile as bq

    calls: list[tuple[list[str], str | None]] = []
    long_sql = "SELECT " + ", ".join(f"{index} AS column_{index}" for index in range(10_000))
    monkeypatch.setattr(
        bq,
        "_run_bq",
        lambda command, *, input_text=None: calls.append((command, input_text)) or {
            "statistics": {"totalBytesProcessed": "123"},
        },
    )

    assert bq.dry_run(long_sql, "project", "US") == 123
    assert long_sql not in calls[0][0]
    assert calls[0][1] == long_sql


def test_missing_dry_run_estimate_is_not_zero(monkeypatch) -> None:
    import pytest
    from data_profile import bigquery_profile as bq
    for payload in [{}, [], {"statistics": {"totalBytesProcessed": -1}}]:
        monkeypatch.setattr(bq, "_run_bq", lambda *_, payload=payload, **__: payload)
        with pytest.raises(bq.ProfilingError):
            bq.dry_run("sql", "project", "US")


def test_bq_failure_preserves_stdout_and_stderr_details(monkeypatch) -> None:
    import pytest
    from data_profile import bigquery_profile as bq

    failure = subprocess.CalledProcessError(
        1,
        ["bq", "query"],
        output="BigQuery error: Invalid query: Unrecognized name: missing_column at [4:12]",
        stderr="ERROR: Query job failed",
    )
    monkeypatch.setattr(bq.subprocess, "run", lambda *_, **__: (_ for _ in ()).throw(failure))

    with pytest.raises(bq.WarehouseError) as captured:
        bq.dry_run("SELECT missing_column FROM broken_view", "project", "US")

    message = str(captured.value)
    assert "BigQuery query failed (exit code 1)" in message
    assert "ERROR: Query job failed" in message
    assert "Unrecognized name: missing_column at [4:12]" in message


def test_type_aliases_generate_metrics() -> None:
    alias_model = model().model_copy(update={"columns": [
        ColumnMetadata(name="integer", data_type="integer"),
        ColumnMetadata(name="float", data_type="FLOAT"),
        ColumnMetadata(name="boolean", data_type="BOOLEAN"),
    ]})
    sql = generate_profile_sql(alias_model)
    assert "MIN(`integer`)" in sql
    assert "MAX(`float`)" in sql
    assert "COUNTIF(`boolean` IS TRUE)" in sql


def test_numeric_types_generate_metrics_and_preserve_decimal_strings() -> None:
    numeric_model = model().model_copy(update={"columns": [
        ColumnMetadata(name="amount", data_type="NUMERIC"),
        ColumnMetadata(name="large_amount", data_type="BIGNUMERIC"),
    ]})

    sql = generate_profile_sql(numeric_model)

    assert "CAST(MIN(`amount`) AS STRING)" in sql
    assert "CAST(MAX(`large_amount`) AS STRING)" in sql

    rows = [
        {
            "dimension_name": None,
            "dimension_value": None,
            "record_count": "1",
            "column_order": str(order),
            "column_name": name,
            "column_type": data_type,
            "null_count": "0",
            "null_rate": "0",
            "distinct_count": None,
            "min_value": value,
            "max_value": value,
            "true_count": None,
        }
        for order, (name, data_type, value) in enumerate([
            ("amount", "NUMERIC", "12345678901234567890.123456789"),
            ("large_amount", "BIGNUMERIC", "-123456789012345678901234567890.12345678901234567890123456789012345678"),
        ])
    ]

    columns = rows_to_profiles(numeric_model, rows, validate=True)[0].columns
    assert columns[0].min_value == "12345678901234567890.123456789"
    assert columns[1].min_value == "-123456789012345678901234567890.12345678901234567890123456789012345678"
