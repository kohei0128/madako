from pathlib import Path

import pytest

from data_profile import DataProfile
from data_profile.dbt_artifacts import ArtifactError, read_dbt_artifacts
from data_profile.repository import DuckDBProfileRepository
from data_profile.storage import build_dbt_artifact_storage


TSUBO_PROJECT = Path(__file__).resolve().parents[3] / "dbt" / "tsubo"


def test_reads_tsubo_models_and_sources() -> None:
    with pytest.warns(UserWarning, match="catalog.json is older"):
        resources = read_dbt_artifacts(TSUBO_PROJECT)

    assert len([item for item in resources if item.resource_type == "model"]) == 10
    assert len([item for item in resources if item.resource_type == "source"]) == 4
    zaim = next(item for item in resources if item.name == "stg_zaim_transactions")
    assert zaim.database == "northern-bliss-362623"
    assert zaim.schema_name == "tsubo_staging"
    assert zaim.materialization == "view"
    assert zaim.profiles == []
    assert zaim.columns[0].name == "as_of_date"
    assert zaim.columns[0].data_type == "DATE"
    assert "not_null" in zaim.tests
    assert zaim.profiling.enabled is True
    assert zaim.profiling.dimensions == ["as_of_date"]
    assert zaim.profiling.max_bytes_billed == 1_000_000_000
    money_forward = next(item for item in resources if item.name == "pl_money_forward")
    assert money_forward.profiling.enabled is True
    assert money_forward.profiling.treat_empty_string_as_null is True


def test_artifact_storage_round_trip(tmp_path: Path) -> None:
    with pytest.warns(UserWarning):
        models_path, profiles_path = build_dbt_artifact_storage(TSUBO_PROJECT, tmp_path)

    repository = DuckDBProfileRepository(models_path, profiles_path)
    resources = repository.list_models()
    assert len(resources) == 14
    assert all(resource.profiles == [] for resource in resources)
    assert repository.get_model("stg_zaim_transactions").columns[10].data_type == "INT64"


def test_public_api_loads_dbt_project_and_builds_plan(tmp_path: Path) -> None:
    with pytest.warns(UserWarning):
        app = DataProfile.from_dbt_project(
            TSUBO_PROJECT,
            tmp_path,
            estimator=lambda *_: 44_762,
        )

    plan = app.plan(select="stg_zaim_transactions")

    assert len(app.models()) == 14
    assert len(plan.items) == 1
    assert plan.items[0].dimension == "as_of_date"
    assert plan.estimated_bytes == 44_762


def test_missing_manifest_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="dbt manifest not found"):
        read_dbt_artifacts(tmp_path)
