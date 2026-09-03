import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from data_profile.repository import DuckDBProfileRepository
from data_profile.server import create_app
from data_profile.storage import build_parquet_fixture


SAMPLE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_profiles.json"


@pytest.fixture
def repository(tmp_path: Path) -> DuckDBProfileRepository:
    models_path, profiles_path = build_parquet_fixture(SAMPLE_FIXTURE, tmp_path)
    return DuckDBProfileRepository(models_path, profiles_path)


def request(app, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(send())


def test_get_model_profile(repository: DuckDBProfileRepository) -> None:
    response = request(create_app(repository), "/api/models/fct_applications/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "fct_applications"
    assert payload["schema"] == "marts"
    assert payload["profiles"][0]["record_count"] == 12480
    assert payload["profiles"][0]["columns"][2]["null_rate"] == 0.15
    assert payload["profiles"][0]["columns"][2]["empty_string_count"] == 0
    assert payload["profiles"][0]["columns"][2]["missing_rate"] == 0.15
    assert payload["profiles"][0]["columns"][2]["min_value"] == 0.04


def test_date_and_categorical_profiles_are_reconstructed(repository: DuckDBProfileRepository) -> None:
    model = repository.get_model("fct_applications")

    date_profiles = [profile for profile in model.profiles if profile.dimension_name == "created_date"]
    service_profiles = [profile for profile in model.profiles if profile.dimension_name == "service"]
    assert len(date_profiles) == 45
    assert date_profiles[-1].dimension_value == "2026-08-30"
    assert {profile.dimension_value for profile in service_profiles} == {"consumer", "business"}


def test_unknown_model_returns_404(repository: DuckDBProfileRepository) -> None:
    response = request(create_app(repository), "/api/models/unknown/profile")

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
