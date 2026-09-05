import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

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


def test_same_name_api_uses_unique_id(tmp_path: Path) -> None:
    first = sample_models()[0]
    second = first.model_copy(update={"unique_id": "source.demo.events", "profiles": [], "profiled_at": None})
    write_profile_storage([first, second], tmp_path)
    storage = ParquetProfileStorage(tmp_path)
    app = create_app(storage)
    assert request(app, "/api/models/events/profile").status_code == 409
    assert request(app, "/api/models/model.demo.events/profile").json()["profiles"]
    assert request(app, "/api/models/source.demo.events/profile").json()["profiles"] == []
