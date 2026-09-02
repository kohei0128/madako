import pytest

from data_profile.bigquery_profile import ProfilingError
from data_profile.models import ColumnMetadata, ModelProfile, ProfilingConfig
from data_profile.planning import create_profile_plan


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
    assert calls[0][1:] == ("project", "asia-northeast1")


def test_plan_marks_estimate_over_limit_as_blocked() -> None:
    plan = create_profile_plan([configured_model()], estimator=lambda *_: 10_001)

    assert plan[0].executable is False


def test_plan_requires_enabled_selection() -> None:
    with pytest.raises(ProfilingError, match="selector did not match"):
        create_profile_plan([configured_model()], selector="missing", estimator=lambda *_: 0)
