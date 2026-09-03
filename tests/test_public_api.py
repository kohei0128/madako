from pathlib import Path

from data_profile import DataProfile, ProfilePlan, ProfileResult
from data_profile.models import ColumnMetadata, ModelProfile, ProfilingConfig
from data_profile.storage import write_profile_storage


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
        ],
        profiling=ProfilingConfig(
            enabled=True,
            dimensions=["event_date"],
            max_bytes_billed=10_000,
        ),
    )


def profile_rows() -> list[dict]:
    rows = []
    for dimension_name, dimension_value in [(None, None), ("event_date", "2026-09-01")]:
        for order, (name, data_type, minimum, maximum) in enumerate([
            ("event_date", "DATE", "2026-09-01", "2026-09-01"),
            ("amount", "INT64", "1", "10"),
        ]):
            rows.append({
                "dimension_name": dimension_name,
                "dimension_value": dimension_value,
                "record_count": "10",
                "column_order": str(order),
                "column_name": name,
                "column_type": data_type,
                "null_count": "0",
                "null_rate": "0",
                "distinct_count": None,
                "min_value": minimum,
                "max_value": maximum,
                "true_count": None,
            })
    return rows


def test_public_api_plans_profiles_and_persists_results(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    estimator_calls: list[str] = []
    runner_calls: list[str] = []
    app = DataProfile.from_storage(
        tmp_path,
        estimator=lambda sql, *_: estimator_calls.append(sql) or 1_000,
        runner=lambda sql, *_: runner_calls.append(sql) or profile_rows(),
    )

    plan = app.plan(select="events")

    assert isinstance(plan, ProfilePlan)
    assert plan.executable is True
    assert plan.estimated_bytes == 1_000
    assert len(estimator_calls) == 1

    result = app.run(plan)

    assert isinstance(result, ProfileResult)
    assert result.successful is True
    assert result.storage_updated is True
    assert result.items[0].status == "succeeded"
    assert result.items[0].row_count == len(profile_rows())
    assert result.profiled_models == ("events",)
    assert len(runner_calls) == 1
    assert result.models_path.exists()
    assert result.profiles_path.exists()
    persisted = app.models()[0]
    assert persisted.profiled_at is not None
    assert len(persisted.profiles) == 2


def test_profile_is_plan_and_run_shortcut(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    app = DataProfile(
        tmp_path,
        estimator=lambda *_: 1_000,
        runner=lambda *_: profile_rows(),
    )

    result = app.profile(select="events")

    assert result.profiled_models == ("events",)
    assert result.plan.items[0].dimension == "event_date"


def test_failed_profile_returns_result_without_updating_storage(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    models_before = (tmp_path / "models.parquet").read_bytes()
    profiles_before = (tmp_path / "column_profiles.parquet").read_bytes()
    app = DataProfile(
        tmp_path,
        estimator=lambda *_: 1_000,
        runner=lambda *_: (_ for _ in ()).throw(RuntimeError("warehouse unavailable")),
    )

    result = app.profile(select="events")

    assert result.successful is False
    assert result.storage_updated is False
    assert result.profiled_models == ()
    assert result.failed[0].error == "warehouse unavailable"
    assert result.skipped == ()
    assert (tmp_path / "models.parquet").read_bytes() == models_before
    assert (tmp_path / "column_profiles.parquet").read_bytes() == profiles_before
