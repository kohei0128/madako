from pathlib import Path

import pytest

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
            return profile_rows() if self.calls == 1 else bad_rows

    adapter = FakeWarehouse()
    app = DataProfile.from_storage(tmp_path, adapter=adapter)
    result = app.profile(project="billing", location="US")
    assert [item.status for item in result.items] == ["succeeded", "failed", "skipped"]
    assert adapter.calls == 2
    assert not result.storage_updated
    assert not result.successful
    assert result.profiled_models == ()
    assert result.failed[0].error
    assert [path.read_bytes() for path in paths] == before


def test_storage_error_remains_exception(tmp_path: Path, monkeypatch) -> None:
    write_profile_storage([configured_model()], tmp_path)
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda *_: profile_rows())

    def fail_save(*args):
        raise OSError("disk full")

    monkeypatch.setattr("data_profile.storage.ParquetProfileStorage.save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        app.profile()


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

    def run(*args):
        if failure:
            raise RuntimeError("query failure")
        return profile_rows()

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
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda *_: calls.append(True) or profile_rows())
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
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda *_: profile_rows())
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
    result = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda *_: rows).profile()
    assert not result.successful
    assert result.failed
    assert [(tmp_path / name).read_bytes() for name in ["models.parquet", "column_profiles.parquet"]] == before


def test_null_date_bucket_is_saved_separately_from_overall(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    rows = profile_rows()
    for row in rows[2:]:
        row["dimension_value"] = None
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda *_: rows)
    assert app.profile().successful
    profiles = app.models()[0].profiles
    assert [(profile.dimension_name, profile.dimension_value) for profile in profiles] == [(None, None), ("event_date", None)]


@pytest.mark.parametrize("changed", [False, True])
def test_reimport_preserves_only_compatible_profiles(tmp_path: Path, monkeypatch, changed: bool) -> None:
    from data_profile.storage import ParquetProfileStorage, import_dbt_profiles
    model = configured_model()
    write_profile_storage([model], tmp_path)
    app = DataProfile(tmp_path, estimator=lambda *_: 1, runner=lambda *_: profile_rows())
    assert app.profile().successful
    model.profiling.treat_empty_string_as_null = changed
    monkeypatch.setattr("data_profile.storage.read_dbt_artifacts", lambda _: [model])
    import_dbt_profiles(tmp_path, ParquetProfileStorage(tmp_path))
    persisted = app.models()[0]
    assert bool(persisted.profiles) is not changed
    assert (persisted.profiled_at is not None) is not changed


def test_empty_table_has_overall_without_date_buckets(tmp_path: Path) -> None:
    write_profile_storage([configured_model()], tmp_path)
    rows = profile_rows()[:2]
    for row in rows:
        row.update(record_count="0", min_value=None, max_value=None, null_rate=None)
    app = DataProfile(tmp_path, estimator=lambda *_: 0, runner=lambda *_: rows)
    assert app.profile().successful
    persisted = app.models()[0]
    assert len(persisted.profiles) == 1
    assert persisted.profiles[0].record_count == 0
