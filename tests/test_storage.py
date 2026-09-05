import os
from pathlib import Path

import pytest

from data_profile.models import ColumnMetadata, ModelProfile
from data_profile.storage import MODELS_FILENAME, PROFILES_FILENAME, write_profile_storage
from data_profile.storage import ParquetProfileStorage


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


def test_incomplete_storage_is_not_treated_as_new(tmp_path: Path) -> None:
    storage = ParquetProfileStorage(tmp_path)
    assert not storage.exists()
    (tmp_path / MODELS_FILENAME).touch()
    with pytest.raises(ValueError, match="incomplete profile storage"):
        storage.exists()


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


def test_failed_rollback_keeps_recovery_copies(tmp_path: Path, monkeypatch) -> None:
    from data_profile.storage import StorageRecoveryError
    paths = write_profile_storage([model("before")], tmp_path)
    before = [path.read_bytes() for path in paths]
    real_replace = os.replace
    calls = 0

    def fail_replace(source, target):
        nonlocal calls
        calls += 1
        if calls in (2, 3):
            raise OSError("replacement failure")
        real_replace(source, target)

    monkeypatch.setattr("data_profile.storage.os.replace", fail_replace)
    with pytest.raises(StorageRecoveryError) as error:
        write_profile_storage([model("after")], tmp_path)
    directory = error.value.recovery_dir
    for path, previous in zip(paths, before):
        assert (directory / f"{path.name}.backup").read_bytes() == previous
    assert str(directory) in str(error.value)


def test_same_name_relations_do_not_share_profiles(tmp_path: Path) -> None:
    from data_profile.sample import sample_models
    from data_profile.repository import AmbiguousProfileError, DuckDBProfileRepository
    first = sample_models()[0]
    second = first.model_copy(update={"unique_id": "source.demo.raw.events", "schema_name": "raw", "profiles": [], "profiled_at": None})
    paths = write_profile_storage([first, second], tmp_path)
    repository = DuckDBProfileRepository(*paths)
    assert repository.get_model(first.unique_id).profiles
    assert repository.get_model(second.unique_id).profiles == []
    with pytest.raises(AmbiguousProfileError):
        repository.get_model("events")


def test_empty_storage_round_trip(tmp_path: Path) -> None:
    storage = ParquetProfileStorage(tmp_path)
    storage.save([])
    assert storage.load() == []


def test_legacy_storage_migrates_only_when_names_are_unambiguous(tmp_path: Path) -> None:
    import duckdb
    from data_profile.sample import sample_models
    storage = ParquetProfileStorage(tmp_path)
    storage.save(sample_models())
    models_path, profiles_path = storage.paths
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE models AS SELECT * EXCLUDE(schema_version) FROM read_parquet(?)", [str(models_path)])
        connection.execute("CREATE TABLE profiles AS SELECT m.model_name, p.* EXCLUDE(schema_version, unique_id) FROM read_parquet(?) p JOIN models m USING(unique_id)", [str(profiles_path)])
        connection.execute("COPY models TO ? (FORMAT PARQUET)", [str(models_path)])
        connection.execute("COPY profiles TO ? (FORMAT PARQUET)", [str(profiles_path)])
    legacy = storage.load()
    assert legacy[0].profiles == sample_models()[0].profiles
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE models AS SELECT * FROM read_parquet(?)", [str(models_path)])
        connection.execute("INSERT INTO models SELECT * REPLACE ('source.demo.raw.events' AS unique_id) FROM models")
        connection.execute("COPY models TO ? (FORMAT PARQUET)", [str(models_path)])
    with pytest.raises(ValueError, match="ambiguous model names"):
        storage.load()
    storage.save(legacy)
    assert storage.load()[0].profiles == legacy[0].profiles


@pytest.mark.parametrize("filename", [MODELS_FILENAME, PROFILES_FILENAME])
def test_unknown_schema_version_is_rejected(tmp_path: Path, filename: str) -> None:
    import duckdb
    from data_profile.sample import sample_models
    storage = ParquetProfileStorage(tmp_path)
    storage.save(sample_models())
    path = tmp_path / filename
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE future AS SELECT * REPLACE(99 AS schema_version) FROM read_parquet(?)", [str(path)])
        connection.execute("COPY future TO ? (FORMAT PARQUET)", [str(path)])
    with pytest.raises(ValueError, match="unsupported profile storage schema version"):
        storage.load()
