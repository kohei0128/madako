import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from data_profile.api import DataProfile
from data_profile.cli import main as cli_main
from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice, ProfilingConfig
from data_profile.planning import ProfileItemResult, ProfilePlanItem, ProfileProgress
from data_profile.server import create_app
from data_profile.storage import ParquetProfileStorage, build_parquet_fixture, write_profile_storage
from data_profile.sample import sample_models


@pytest.fixture
def storage(tmp_path: Path) -> ParquetProfileStorage:
    write_profile_storage(sample_models(), tmp_path)
    return ParquetProfileStorage(tmp_path)


def request(app, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(send())


def test_get_model_profile(storage: ParquetProfileStorage) -> None:
    response = request(create_app(storage), "/api/models/events/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "events"
    assert payload["schema"] == "analytics"
    assert payload["profiles"][0]["record_count"] == 10
    assert payload["profiles"][0]["columns"][1]["null_rate"] == 0.5
    assert payload["profiles"][0]["columns"][1]["empty_string_count"] == 0
    assert payload["profiles"][0]["columns"][1]["missing_rate"] == 0.5
    assert payload["profiles"][0]["columns"][1]["min_value"] == 1.5
    assert payload["upstream_ids"] == [
        "source.demo.raw_events",
        "source.demo.raw_users",
        "source.demo.raw_campaigns",
    ]
    assert payload["profiles"][0]["columns"][0]["distinct_ratio"] == 0.2


def test_bundled_web_ui_is_served(storage: ParquetProfileStorage) -> None:
    response = request(create_app(storage), "/")

    assert response.status_code == 200
    assert "<title>Madako</title>" in response.text


def test_bundled_web_ui_is_served_for_relation_deep_link(storage: ParquetProfileStorage) -> None:
    response = request(create_app(storage), "/relations/model.demo.events")

    assert response.status_code == 200
    assert "<title>Madako</title>" in response.text


def test_date_and_categorical_profiles_are_reconstructed(storage: ParquetProfileStorage) -> None:
    model = storage.get_model("events")

    date_profiles = [profile for profile in model.profiles if profile.dimension_name == "event_date"]
    service_profiles = [profile for profile in model.profiles if profile.dimension_name == "service"]
    assert len(date_profiles) == 3
    assert {profile.dimension_value for profile in date_profiles} == {"2026-08-31", "2026-09-01", None}
    assert {profile.dimension_value for profile in service_profiles} == {"consumer", "business"}


def test_unknown_model_returns_404(storage: ParquetProfileStorage) -> None:
    response = request(create_app(storage), "/api/models/unknown/profile")

    assert response.status_code == 404


def test_invalid_fixture_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid.json"
    fixture.write_text(
        json.dumps([{
            "name": "broken",
            "database": "project",
            "schema_name": "dataset",
            "materialization": "table",
            "profiled_at": "2026-08-30T00:00:00Z",
            "profiles": [{
                "record_count": 1,
                "columns": [{
                    "name": "id",
                    "data_type": "STRING",
                    "null_count": 2,
                    "null_rate": 1.5,
                }],
            }],
        }]),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        build_parquet_fixture(fixture, tmp_path / "parquet")


def test_metadata_list_omits_metrics(storage: ParquetProfileStorage) -> None:
    response = request(create_app(storage), "/api/models?include_profiles=false")
    assert response.status_code == 200
    assert response.json()[0]["profiles"] == []
    assert response.json()[0]["columns"]
    assert response.json()[0]["profiled_at"]
    assert response.json()[0]["upstream_ids"] == [
        "source.demo.raw_events",
        "source.demo.raw_users",
        "source.demo.raw_campaigns",
    ]


def test_same_name_api_uses_unique_id(tmp_path: Path) -> None:
    first = sample_models()[0]
    second = first.model_copy(update={"unique_id": "source.demo.events", "profiles": [], "profiled_at": None})
    write_profile_storage([first, second], tmp_path)
    storage = ParquetProfileStorage(tmp_path)
    app = create_app(storage)
    assert request(app, "/api/models/events/profile").status_code == 409
    assert request(app, "/api/models/model.demo.events/profile").json()["profiles"]
    assert request(app, "/api/models/source.demo.events/profile").json()["profiles"] == []


def test_cli_serve_uses_profile_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_dir = tmp_path / "profile"
    write_profile_storage(sample_models(), storage_dir)
    (tmp_path / "madako.toml").write_text(
        """
[project]
storage_dir = "profile"

[server]
host = "0.0.0.0"
port = 8123
""".strip(),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def run_server(app, **kwargs) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["madako", "serve"])
    monkeypatch.setattr("uvicorn.run", run_server)

    cli_main()

    app = captured["app"]
    list_models = next(route.endpoint for route in app.routes if route.path == "/api/models")
    models = list_models(include_profiles=False)
    assert models[0].name == "events"
    assert models[0].profiles == []
    assert captured["kwargs"] == {"host": "0.0.0.0", "port": 8123}


def test_cli_build_sample_discovers_parent_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "madako.toml").write_text(
        """
[project]
storage_dir = ".madako"

[storage]
mode = "generations"
keep_generations = 2
""".strip(),
        encoding="utf-8",
    )
    child = tmp_path / "models"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.setattr(sys, "argv", ["madako", "build-sample"])

    cli_main()

    storage = ParquetProfileStorage(
        tmp_path / ".madako",
        use_generations=True,
        keep_generations=2,
    )
    assert storage.exists()
    assert (tmp_path / ".madako" / "current").is_symlink()


def test_cli_options_override_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_storage = tmp_path / "configured"
    override_storage = tmp_path / "override"
    write_profile_storage(sample_models(), override_storage)
    (tmp_path / "madako.toml").write_text(
        "[project]\nstorage_dir = 'configured'\n[server]\nport = 8123\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def run_server(app, **kwargs) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["madako", "serve", "--storage-dir", str(override_storage), "--port", "9000"],
    )
    monkeypatch.setattr("uvicorn.run", run_server)

    cli_main()

    app = captured["app"]
    list_models = next(route.endpoint for route in app.routes if route.path == "/api/models")
    assert list_models(include_profiles=False)[0].name == "events"
    assert captured["kwargs"] == {"host": "127.0.0.1", "port": 9000}
    assert not configured_storage.exists()


def test_cli_profile_imports_dbt_artifacts_before_profiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_dir = tmp_path / "dbt-project"
    storage_dir = tmp_path / ".madako"
    (tmp_path / "madako.toml").write_text(
        "[project]\ndbt_project_dir = 'dbt-project'\nstorage_dir = '.madako'\n",
        encoding="utf-8",
    )
    calls: list[object] = []
    plan = SimpleNamespace(items=())
    skipped = SimpleNamespace(
        item=SimpleNamespace(
            model=SimpleNamespace(name="events", unique_id="model.demo.events"),
            dimension="user_id",
        ),
        status="skipped",
        error="dimension cardinality exceeds limit",
        skip_reason="max_dimension_values",
        distinct_values=1_284_392,
        maximum_allowed=10_000,
    )
    result = SimpleNamespace(
        items=(skipped,),
        successful=True,
        profiled_models=(),
        plan=plan,
    )

    def plan_profiles(*, progress, **kwargs):
        calls.append(("plan", kwargs))
        return plan

    def run_profiles(received_plan, *, progress, threads):
        calls.append(("run", received_plan, threads))
        progress(ProfileProgress(
            event="execute_skipped",
            current=1,
            total=1,
            model=skipped.item.model,
            dimension=skipped.item.dimension,
            item=skipped.item,
            result=skipped,
        ))
        return result

    app = SimpleNamespace(
        storage_dir=storage_dir,
        plan=plan_profiles,
        run=run_profiles,
    )

    def import_dbt(
        received_project_dir: Path,
        received_storage_dir: Path,
        *,
        storage: ParquetProfileStorage,
    ) -> SimpleNamespace:
        assert storage.directory == storage_dir
        calls.append(("import", received_project_dir, received_storage_dir))
        return app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["madako", "profile", "--select", "events", "--threads", "8"],
    )
    monkeypatch.setattr(DataProfile, "from_dbt_project", import_dbt)

    cli_main()

    assert calls[0] == ("import", project_dir, storage_dir)
    assert calls[1] == (
        "plan",
        {"select": "events", "project": None, "location": "asia-northeast1"},
    )
    assert calls[2] == ("run", plan, 8)
    output = capsys.readouterr().out
    assert "Importing dbt artifacts" in output
    assert "Imported dbt artifacts" in output
    assert "Planning queries" in output
    assert "Warning: Skipped model.demo.events / user_id" in output
    assert "1,284,392 distinct values exceeds the 10,000 limit" in output
    assert "Profile complete: 0 relations, 0 queries, 1 skipped" in output


def test_cli_logs_query_byte_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from data_profile.progress import ProfileRenderer

    model = ModelProfile(
        name="events",
        database="project",
        schema_name="dataset",
        materialization="table",
        profiling=ProfilingConfig(enabled=True),
    )
    item = ProfilePlanItem(
        model=model,
        sql="SELECT 1",
        project="project",
        location="US",
        model_signature=model.profiling_signature(),
    )
    result = ProfileItemResult(
        item=item,
        status="succeeded",
        row_count=3,
        bytes_processed=12_345,
        bytes_billed=10_485_760,
    )

    ProfileRenderer(tmp_path, verbose=True).event(ProfileProgress(
        event="execute_completed",
        current=1,
        total=1,
        item=item,
        result=result,
    ))

    output = capsys.readouterr().out
    assert "12.1 KiB processed" in output
    assert "billed" not in output


def test_cli_reports_failed_query_once_before_abort(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from data_profile.progress import ProfileRenderer

    model = ModelProfile(
        unique_id="model.demo.broken_view",
        name="broken_view",
        database="project",
        schema_name="dataset",
        materialization="view",
        profiling=ProfilingConfig(enabled=True),
    )
    item = ProfilePlanItem(
        model=model,
        dimension="event_date",
        sql="SELECT 1",
        project="project",
        location="US",
        model_signature=model.profiling_signature(),
    )
    error = "Invalid query: Unrecognized name: missing_column at [4:12]"
    result = ProfileItemResult(item=item, status="failed", error=error)

    renderer = ProfileRenderer(tmp_path)
    renderer.event(ProfileProgress(
        event="execute_completed", current=1, total=1, item=item, result=result,
    ))
    renderer.event(ProfileProgress(
        event="storage_discarded",
        current=0,
        total=1,
        model=model,
        dimension=item.dimension,
        item=item,
        result=result,
        error=error,
    ))

    output = capsys.readouterr().out
    assert "Profile results not saved" in output
    assert "Error: Failed model.demo.broken_view / event_date" in output
    assert output.count(error) == 1


@pytest.mark.parametrize(("value", "expected"), [
    (0, "0 B"),
    (1_023, "1,023 B"),
    (1_024, "1.0 KiB"),
    (5 * 1024**2, "5.0 MiB"),
    (2 * 1024**3, "2.0 GiB"),
])
def test_cli_formats_byte_units(value: int, expected: str) -> None:
    from data_profile.progress import format_bytes

    assert format_bytes(value) == expected


def test_numeric_values_are_serialized_as_exact_strings(tmp_path: Path) -> None:
    numeric = sample_models()[0].model_copy(update={
        "columns": [ColumnMetadata(name="amount", data_type="BIGNUMERIC")],
        "profiles": [ProfileSlice(
            record_count=1,
            columns=[ColumnProfile(
                name="amount",
                data_type="BIGNUMERIC",
                null_count=0,
                null_rate=0,
                min_value="-123456789012345678901234567890.12345678901234567890123456789012345678",
                max_value="123456789012345678901234567890.12345678901234567890123456789012345678",
            )],
        )],
    })
    write_profile_storage([numeric], tmp_path)

    response = request(create_app(ParquetProfileStorage(tmp_path)), "/api/models/events/profile")

    assert response.status_code == 200
    metric = response.json()["profiles"][0]["columns"][0]
    assert metric["min_value"] == numeric.profiles[0].columns[0].min_value
    assert metric["max_value"] == numeric.profiles[0].columns[0].max_value
