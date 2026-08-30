import json
from pathlib import Path

from data_profile.models import ModelProfile


class ProfileNotFoundError(Exception):
    pass


class JsonProfileRepository:
    def __init__(self, fixture_path: Path):
        self.fixture_path = fixture_path

    def list_models(self) -> list[ModelProfile]:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return [ModelProfile.model_validate(item) for item in payload]

    def get_model(self, model_name: str) -> ModelProfile:
        try:
            return next(model for model in self.list_models() if model.name == model_name)
        except StopIteration as error:
            raise ProfileNotFoundError(model_name) from error

