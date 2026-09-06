from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from data_profile.models import ModelProfile
from data_profile.repository import AmbiguousProfileError, ProfileNotFoundError
from data_profile.storage import ParquetProfileStorage, ProfileStorage


DEFAULT_STORAGE_DIR = Path(".data-profile")
WEB_DIST_DIR = Path(__file__).with_name("web_dist")


def create_app(storage: ProfileStorage | None = None) -> FastAPI:
    app = FastAPI(title="Madako API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    storage = storage or ParquetProfileStorage(DEFAULT_STORAGE_DIR)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/models", response_model=list[ModelProfile], response_model_by_alias=True)
    def list_models(include_profiles: bool = True) -> list[ModelProfile]:
        models = storage.load()
        return models if include_profiles else [model.model_copy(update={"profiles": []}) for model in models]

    @app.get(
        "/api/models/{model_name}/profile",
        response_model=ModelProfile,
        response_model_by_alias=True,
    )
    def get_profile(model_name: str) -> ModelProfile:
        try:
            return storage.get_model(model_name)
        except AmbiguousProfileError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProfileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Model profile not found") from error

    if WEB_DIST_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST_DIR, html=True), name="web")

    return app


app = create_app()
