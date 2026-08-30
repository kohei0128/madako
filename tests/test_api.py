import asyncio
import json
from pathlib import Path

import httpx

from data_profile.server import create_app


def request(app, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(send())


def test_get_model_profile() -> None:
    response = request(create_app(), "/api/models/fct_applications/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "fct_applications"
    assert payload["schema"] == "marts"
    assert payload["profiles"][0]["record_count"] == 12480
    assert payload["profiles"][0]["columns"][2]["null_rate"] == 0.15


def test_unknown_model_returns_404() -> None:
    response = request(create_app(), "/api/models/unknown/profile")

    assert response.status_code == 404


def test_invalid_fixture_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "name": "broken",
                    "database": "project",
                    "schema_name": "dataset",
                    "materialization": "table",
                    "profiled_at": "2026-08-30T00:00:00Z",
                    "profiles": [
                        {
                            "record_count": 1,
                            "columns": [
                                {
                                    "name": "id",
                                    "data_type": "STRING",
                                    "null_count": 2,
                                    "null_rate": 1.5
                                }
                            ]
                        }
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )
    response = request(create_app(fixture), "/api/models")

    assert response.status_code == 500
