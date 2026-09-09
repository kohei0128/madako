from pathlib import Path

import pytest

from data_profile import DataProfile, ProfilePlan, ProfileProgress, ProfileResult
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


def rows_for_sql(sql: str, rows: list[dict] | None = None) -> list[dict]:
    available = rows if rows is not None else profile_rows()
    dimension = "event_date" if "'event_date' AS dimension_name" in sql else None
    return [row for row in available if row.get("dimension_name") == dimension]


def test_public_api_plans_profiles_and_persists_results(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    estimator_calls: list[str] = []
    runner_calls: list[str] = []
    app = DataProfile.from_storage(
        tmp_path,
        estimator=lambda sql, *_: estimator_calls.append(sql) or 1_000,
        runner=lambda sql, *_: runner_calls.append(sql) or rows_for_sql(sql),
    )

    plan = app.plan(select="events")

    assert isinstance(plan, ProfilePlan)
    assert plan.executable is True
    assert plan.estimated_bytes == 2_000
    assert len(estimator_calls) == 2

    result = app.run(plan)

    assert isinstance(result, ProfileResult)
    assert result.successful is True
    assert result.storage_updated is True
    assert result.items[0].status == "succeeded"
    assert result.items[0].row_count == 2
    assert result.profiled_models == ("events",)
    assert len(runner_calls) == 2
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
        runner=lambda sql, *_: rows_for_sql(sql),
    )

    result = app.profile(select="events")

    assert result.profiled_models == ("events",)
    assert [item.dimension for item in result.plan.items] == [None, "event_date"]


def test_cardinality_skip_is_successful_and_saves_overall_profile(tmp_path: Path) -> None:
    model = configured_model().model_copy(update={
        "columns": [ColumnMetadata(name="user_id", data_type="STRING")],
        "profiling": ProfilingConfig(
            enabled=True,
            dimensions=["user_id"],
            max_dimension_values=1,
        ),
    })
    write_profile_storage([model], tmp_path)
    runner_calls: list[str] = []

    def run(sql: str, *_args) -> list[dict]:
        runner_calls.append(sql)
        return [{
            "dimension_name": None,
            "dimension_value": None,
            "record_count": "10",
            "column_order": "0",
            "column_name": "user_id",
            "column_type": "STRING",
            "null_count": "0",
            "null_rate": "0",
            "distinct_count": "2",
            "min_value": None,
            "max_value": None,
            "true_count": None,
        }]

    result = DataProfile(tmp_path, estimator=lambda *_: 1, runner=run).profile()

    assert result.successful is True
    assert result.storage_updated is True
    assert [item.status for item in result.items] == ["succeeded", "skipped"]
    assert len(runner_calls) == 1
    assert [profile.dimension_name for profile in DataProfile(tmp_path).models()[0].profiles] == [None]


def test_profile_reports_planning_execution_and_storage_progress(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    events: list[ProfileProgress] = []

    result = DataProfile(
        tmp_path,
        estimator=lambda *_: 1_000,
        runner=lambda sql, *_: rows_for_sql(sql),
    ).profile(select="events", progress=events.append)

    assert result.successful
    assert [event.event for event in events] == [
        "estimate_started", "estimate_completed",
        "estimate_started", "estimate_completed",
        "execute_started", "execute_completed",
        "execute_started", "execute_completed",
        "storage_started", "storage_completed",
    ]
    assert [(event.current, event.total) for event in events[:4]] == [
        (1, 2), (1, 2), (2, 2), (2, 2),
    ]


def test_failed_profile_returns_result_without_updating_storage(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    models_before = (tmp_path / "models.parquet").read_bytes()
    profiles_before = (tmp_path / "column_profiles.parquet").read_bytes()
    app = DataProfile(
        tmp_path,
        estimator=lambda *_: 1_000,
        runner=lambda *_: (_ for _ in ()).throw(RuntimeError("warehouse unavailable")),
    )

    events: list[ProfileProgress] = []
    result = app.profile(select="events", progress=events.append)

    assert result.successful is False
    assert result.storage_updated is False
    assert result.profiled_models == ()
    assert result.failed[0].error == "warehouse unavailable"
    assert len(result.skipped) == 1
    assert events[-1].event == "storage_discarded"
    assert events[-1].current == 0
    assert (tmp_path / "models.parquet").read_bytes() == models_before
    assert (tmp_path / "column_profiles.parquet").read_bytes() == profiles_before


@pytest.mark.parametrize("bad_rows", [[], [{"column_name": "unknown"}]])
def test_adapter_failure_after_success_preserves_storage(tmp_path: Path, bad_rows: list[dict]) -> None:
    first = configured_model()
    models = [first, first.model_copy(update={"name": "second", "unique_id": "model.test.second"}),
              first.model_copy(update={"name": "third", "unique_id": "model.test.third"})]
    paths = write_profile_storage(models, tmp_path)
    before = [path.read_bytes() for path in paths]

    class FakeWarehouse:
        calls = 0

        def estimate(self, sql, project, location):
            assert (project, location) == ("billing", "US")
            return 100

        def execute(self, sql, project, location, max_bytes_billed):
            assert (project, location, max_bytes_billed) == ("billing", "US", 10_000)
            self.calls += 1
            return rows_for_sql(sql) if self.calls == 1 else bad_rows

    adapter = FakeWarehouse()
    app = DataProfile.from_storage(tmp_path, adapter=adapter)
    events: list[ProfileProgress] = []
    result = app.profile(project="billing", location="US", progress=events.append)
    assert [item.status for item in result.items] == ["succeeded", "failed"] + ["skipped"] * 4
    assert adapter.calls == 2
    assert not result.storage_updated
    assert not result.successful
    assert result.profiled_models == ()
    assert result.failed[0].error
    assert events[-1].event == "storage_discarded"
    assert events[-1].current == 1
    assert [path.read_bytes() for path in paths] == before


def test_storage_error_remains_exception(tmp_path: Path, monkeypatch) -> None:
    from data_profile import StorageOperationError

    write_profile_storage([configured_model()], tmp_path)
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: rows_for_sql(sql))

    def fail_save(*args):
        raise OSError("disk full")

    monkeypatch.setattr("data_profile.storage.ParquetProfileStorage.save", fail_save)
    with pytest.raises(StorageOperationError, match="could not save profile storage") as error:
        app.profile()
    assert isinstance(error.value.__cause__, OSError)


@pytest.mark.parametrize("failure", [False, True])
def test_custom_storage_receives_only_complete_results(tmp_path: Path, failure: bool) -> None:
    class MemoryStorage:
        paths = (tmp_path / "custom-models", tmp_path / "custom-profiles")
        saved = 0

        def __init__(self):
            self.data = [configured_model()]

        def exists(self):
            return True

        def load(self):
            return self.data

        def save(self, models):
            self.saved += 1
            self.data = models
            return self.paths

    storage = MemoryStorage()

    def run(sql, *_args):
        if failure:
            raise RuntimeError("query failure")
        return rows_for_sql(sql)

    app = DataProfile.from_storage(tmp_path, storage=storage, estimator=lambda *_: 1, runner=run)
    result = app.profile()
    assert storage.saved == (0 if failure else 1)
    assert result.successful is not failure
    assert (result.models_path, result.profiles_path) == storage.paths
    assert bool(app.models()[0].profiles) is not failure
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["remove", "config", "schema", "mutate_plan"])
def test_stale_plan_never_executes(tmp_path: Path, change: str) -> None:
    from data_profile import ProfilingError
    model = configured_model()
    write_profile_storage([model], tmp_path)
    calls = []
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: calls.append(True) or rows_for_sql(sql))
    plan = app.plan()
    if change == "remove":
        write_profile_storage([], tmp_path)
    elif change == "config":
        model.profiling.max_bytes_billed = 1
        write_profile_storage([model], tmp_path)
    elif change == "schema":
        model.columns.pop()
        write_profile_storage([model], tmp_path)
    else:
        plan.items[0].model.columns.pop()
    with pytest.raises(ProfilingError, match="stale or modified plan"):
        app.run(plan)
    assert not calls


@pytest.mark.parametrize("bad_result", ["column", "duplicate", "bucket", "record_count", "type", "rate"])
def test_incomplete_results_preserve_existing_storage(tmp_path: Path, bad_result: str) -> None:
    write_profile_storage([configured_model()], tmp_path)
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: rows_for_sql(sql))
    assert app.profile().successful
    before = [(tmp_path / name).read_bytes() for name in ["models.parquet", "column_profiles.parquet"]]
    rows = profile_rows()
    if bad_result == "column":
        rows.pop()
    elif bad_result == "duplicate":
        rows.append(rows[-1].copy())
    elif bad_result == "bucket":
        rows = rows[:2]
    elif bad_result == "record_count":
        rows[-1]["record_count"] = "5"
    elif bad_result == "type":
        rows[-1]["column_type"] = "STRING"
    else:
        rows[-1]["null_rate"] = "0.5"
    result = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: rows_for_sql(sql, rows)).profile()
    assert not result.successful
    assert result.failed
    assert [(tmp_path / name).read_bytes() for name in ["models.parquet", "column_profiles.parquet"]] == before


def test_null_date_bucket_is_saved_separately_from_overall(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    rows = profile_rows()
    for row in rows[2:]:
        row["dimension_value"] = None
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: rows_for_sql(sql, rows))
    assert app.profile().successful
    profiles = app.models()[0].profiles
    assert [(profile.dimension_name, profile.dimension_value) for profile in profiles] == [(None, None), ("event_date", None)]


@pytest.mark.parametrize("changed", [False, True])
def test_reimport_preserves_only_compatible_profiles(tmp_path: Path, monkeypatch, changed: bool) -> None:
    from data_profile.storage import ParquetProfileStorage, import_dbt_profiles
    model = configured_model()
    write_profile_storage([model], tmp_path)
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: rows_for_sql(sql))
    assert app.profile().successful
    model.profiling.treat_empty_string_as_null = changed
    monkeypatch.setattr("data_profile.storage.read_dbt_artifacts", lambda _: [model])
    import_dbt_profiles(tmp_path, ParquetProfileStorage(tmp_path))
    persisted = app.models()[0]
    assert bool(persisted.profiles) is not changed
    assert (persisted.profiled_at is not None) is not changed


def test_reimport_invalidates_profiles_from_old_computation_version(tmp_path: Path, monkeypatch) -> None:
    from data_profile.storage import ParquetProfileStorage, import_dbt_profiles
    current = configured_model()
    write_profile_storage([current], tmp_path)
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda sql, *_: rows_for_sql(sql))
    assert app.profile().successful
    profiled = app.models()[0].model_copy(update={"profile_version": 1})
    write_profile_storage([profiled], tmp_path)
    monkeypatch.setattr("data_profile.storage.read_dbt_artifacts", lambda _: [current])

    import_dbt_profiles(tmp_path, ParquetProfileStorage(tmp_path))

    persisted = app.models()[0]
    assert persisted.profiles == []
    assert persisted.profiled_at is None
    assert persisted.profile_version is None


def test_empty_table_has_overall_without_date_buckets(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    rows = profile_rows()[:2]
    for row in rows:
        row.update(record_count="0", min_value=None, max_value=None, null_rate=None)
    app = DataProfile(tmp_path, estimator=lambda *_: 0, runner=lambda sql, *_: rows_for_sql(sql, rows))
    assert app.profile().successful
    persisted = app.models()[0]
    assert len(persisted.profiles) == 1
    assert persisted.profiles[0].record_count == 0
