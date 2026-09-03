import os
from pathlib import Path

import pytest

from data_profile.models import ColumnMetadata, ModelProfile
from data_profile.storage import MODELS_FILENAME, PROFILES_FILENAME, write_profile_storage


def model(description: str) -> ModelProfile:
    return ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        relation_name="`project.dataset.events`",
        description=description,
        materialization="table",
        columns=[ColumnMetadata(name="id", data_type="INT64")],
    )


def test_storage_replacement_restores_previous_files_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_path, profiles_path = write_profile_storage([model("before")], tmp_path)
    previous_models = models_path.read_bytes()
    previous_profiles = profiles_path.read_bytes()
    real_replace = os.replace
    calls = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        real_replace(source, target)

    monkeypatch.setattr("data_profile.storage.os.replace", fail_second_replace)

    with pytest.raises(OSError, match="simulated replacement failure"):
        write_profile_storage([model("after")], tmp_path)

    assert (tmp_path / MODELS_FILENAME).read_bytes() == previous_models
    assert (tmp_path / PROFILES_FILENAME).read_bytes() == previous_profiles
    assert not list(tmp_path.glob(".profile-stage-*"))
