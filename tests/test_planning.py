import pytest

from data_profile.bigquery_profile import ProfilingError
from data_profile.models import ColumnMetadata, ModelProfile, ProfilingConfig
from data_profile.planning import create_profile_plan, execute_profile_plan


def configured_model() -> ModelProfile:
    return ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        relation_name="`project.dataset.events`",
        materialization="table",
        columns=[
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="amount", data_type="INT64"),
            ColumnMetadata(name="payload", data_type="JSON"),
        ],
        profiling=ProfilingConfig(
            enabled=True,
            dimensions=["event_date"],
            max_bytes_billed=10_000,
        ),
    )


def test_plan_resolves_config_and_estimates_cost() -> None:
    calls: list[tuple[str, str, str]] = []

    def estimate(sql: str, project: str, location: str) -> int:
        calls.append((sql, project, location))
        return 8_000

    plan = create_profile_plan([configured_model()], estimator=estimate)

    assert len(plan) == 1
    assert plan[0].dimension == "event_date"
    assert plan[0].estimated_bytes == 8_000
    assert plan[0].executable is True
    assert plan[0].skipped_columns == ("payload",)
    assert plan[0].project == "project"
    assert plan[0].location == "asia-northeast1"
    assert calls[0][1:] == ("project", "asia-northeast1")


def test_plan_marks_estimate_over_limit_as_blocked() -> None:
    plan = create_profile_plan([configured_model()], estimator=lambda *_: 10_001)

    assert plan[0].executable is False


def test_plan_requires_enabled_selection() -> None:
    with pytest.raises(ProfilingError, match="selector did not match"):
        create_profile_plan([configured_model()], selector="missing", estimator=lambda *_: 0)


def test_plan_supports_overall_only_profile() -> None:
    model = configured_model().model_copy(update={
        "profiling": ProfilingConfig(enabled=True, dimensions=[]),
    })

    plan = create_profile_plan([model], estimator=lambda *_: 1)

    assert plan[0].dimension is None
    assert "dimension_agg" not in plan[0].sql


def test_execute_plan_combines_multiple_dimensions_and_updates_once() -> None:
    model = configured_model()
    model = model.model_copy(update={
        "columns": [
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="processed_date", data_type="DATE"),
        ],
        "profiling": ProfilingConfig(
            enabled=True,
            dimensions=["event_date", "processed_date"],
            max_bytes_billed=10_000,
        ),
    })
    plan = create_profile_plan([model], estimator=lambda *_: 1)
    calls: list[tuple[str, str, int]] = []

    def run(sql: str, project: str, location: str, maximum: int) -> list[dict]:
        calls.append((project, location, maximum))
        dimension = "processed_date" if "'processed_date' AS dimension_name" in sql else "event_date"
        rows = []
        for dimension_name, dimension_value in [(None, None), (dimension, "2026-09-01")]:
            for order, column in enumerate(model.columns):
                rows.append({
                    "dimension_name": dimension_name,
                    "dimension_value": dimension_value,
                    "record_count": "10",
                    "column_order": str(order),
                    "column_name": column.name,
                    "column_type": column.data_type,
                    "null_count": "0",
                    "null_rate": "0",
                    "distinct_count": None,
                    "min_value": "2026-09-01",
                    "max_value": "2026-09-01",
                    "true_count": None,
                })
        return rows

    updated = execute_profile_plan([model], plan, runner=run)

    assert len(calls) == 2
    assert [profile.dimension_name for profile in updated[0].profiles] == [
        None,
        "event_date",
        "processed_date",
    ]
    assert updated[0].profiled_at is not None


def test_execute_plan_rejects_all_work_before_running_blocked_item() -> None:
    plan = create_profile_plan([configured_model()], estimator=lambda *_: 10_001)
    calls: list[str] = []

    with pytest.raises(ProfilingError, match="over max_bytes_billed"):
        execute_profile_plan([configured_model()], plan, runner=lambda sql, *_: calls.append(sql) or [])

    assert calls == []
