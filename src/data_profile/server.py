from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from data_profile.models import ModelProfile
from data_profile.repository import JsonProfileRepository, ProfileNotFoundError


DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sample_profiles.json"


def create_app(fixture_path: Path = DEFAULT_FIXTURE) -> FastAPI:
    app = FastAPI(title="Data Profile API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    repository = JsonProfileRepository(fixture_path)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/models", response_model=list[ModelProfile], response_model_by_alias=True)
    async def list_models() -> list[ModelProfile]:
        return repository.list_models()

    @app.get(
        "/api/models/{model_name}/profile",
        response_model=ModelProfile,
        response_model_by_alias=True,
    )
    async def get_profile(model_name: str) -> ModelProfile:
        try:
            return repository.get_model(model_name)
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Model profile not found") from error

    return app


app = create_app()
