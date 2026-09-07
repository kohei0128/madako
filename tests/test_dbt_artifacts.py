import json
from pathlib import Path

import pytest

from data_profile import DataProfile
from data_profile.dbt_artifacts import ArtifactError, read_dbt_artifacts
from data_profile.repository import DuckDBProfileRepository
from data_profile.storage import ParquetProfileStorage


@pytest.fixture
def dbt_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    target = project / "target"
    target.mkdir(parents=True)
    model = {
        "unique_id": "model.demo.events", "resource_type": "model", "package_name": "demo",
        "name": "events", "alias": "events", "database": "project", "schema": "analytics",
        "columns": {"event_date": {"data_type": "DATE", "description": "Event date"},
                    "amount": {"data_type": "INTEGER"}},
        "depends_on": {"nodes": ["source.demo.raw.events"]},
        "config": {"materialized": "view", "meta": {"profiling": {
            "enabled": True, "dimensions": ["event_date"], "max_bytes_billed": 1000,
        }}},
    }
    source = {**model, "unique_id": "source.demo.raw.events", "resource_type": "source",
              "identifier": "raw_events", "schema": "raw",
              "depends_on": {"nodes": []},
              "config": {"meta": {"profiling": {"enabled": True, "treat_empty_string_as_null": True}}}}
    manifest = {
        "metadata": {"project_name": "demo", "generated_at": "2026-09-01T00:00:00Z"},
        "nodes": {model["unique_id"]: model,
                  "model.other.ignored": {**model, "unique_id": "model.other.ignored", "package_name": "other"},
                  "test.demo.date": {"resource_type": "test", "test_metadata": {"name": "not_null"},
                                     "depends_on": {"nodes": [model["unique_id"]]}}},
        "sources": {source["unique_id"]: source},
    }
    catalog = {
        "metadata": {"generated_at": "2026-08-31T00:00:00Z"},
        "nodes": {model["unique_id"]: {"columns": {
            "amount": {"type": "INTEGER", "index": 2}, "event_date": {"type": "DATE", "index": 1},
        }}},
    }
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "catalog.json").write_text(json.dumps(catalog))
    return project


def test_reads_models_sources_and_catalog(dbt_project: Path) -> None:
    with pytest.warns(UserWarning, match="catalog.json is older"):
        resources = read_dbt_artifacts(dbt_project)
    assert len(resources) == 2
    model = next(item for item in resources if item.resource_type == "model")
    source = next(item for item in resources if item.resource_type == "source")
    assert model.materialization == "view"
    assert model.profiles == []
    assert [(column.name, column.data_type) for column in model.columns] == [("event_date", "DATE"), ("amount", "INT64")]
    assert model.tests == ["not_null"]
    assert model.upstream_ids == ["source.demo.raw.events"]
    assert model.profiling.dimensions == ["event_date"]
    assert model.profiling.max_bytes_billed == 1000
    assert source.profiling.treat_empty_string_as_null
    assert source.relation_name == "`project.raw.raw_events`"


def test_artifact_storage_round_trip(dbt_project: Path, tmp_path: Path) -> None:
    with pytest.warns(UserWarning):
        DataProfile.from_dbt_project(dbt_project, tmp_path / "storage")
        paths = ParquetProfileStorage(tmp_path / "storage").paths
    repository = DuckDBProfileRepository(*paths)
    assert len(repository.list_models()) == 2
    assert all(resource.profiles == [] for resource in repository.list_models())
    assert repository.get_model("model.demo.events").columns[1].data_type == "INT64"
    assert repository.get_model("model.demo.events").upstream_ids == ["source.demo.raw.events"]


def test_public_api_loads_dbt_project_and_builds_plan(dbt_project: Path, tmp_path: Path) -> None:
    with pytest.warns(UserWarning):
        app = DataProfile.from_dbt_project(dbt_project, tmp_path / "storage", estimator=lambda *_: 100)
    plan = app.plan(select="model.demo.events")
    assert len(app.models()) == 2
    assert plan.items[0].dimension == "event_date"
    assert plan.estimated_bytes == 100


def test_manifest_only_fallback(dbt_project: Path) -> None:
    (dbt_project / "target" / "catalog.json").unlink()
    resources = read_dbt_artifacts(dbt_project)
    assert resources[0].columns[1].data_type == "INT64"


def test_missing_manifest_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="dbt manifest not found"):
        read_dbt_artifacts(tmp_path)
