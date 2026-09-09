import os
from pathlib import Path

import pytest

from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice
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


def test_csv_special_characters_round_trip_without_staging_files(tmp_path: Path) -> None:
    special = 'comma, quote " and newline\n日本語'
    expected = model(special).model_copy(
        update={
            "columns": [
                ColumnMetadata(
                    name="category", data_type="STRING", description=special
                )
            ],
            "profiles": [
                ProfileSlice(
                    dimension_name="category",
                    dimension_value=special,
                    record_count=1,
                    columns=[
                        ColumnProfile(
                            name="category",
                            data_type="STRING",
                            description=special,
                            null_count=0,
                            null_rate=0,
                            distinct_count=1,
                            min_value=special,
                            max_value="",
                        )
                    ],
                )
            ],
        }
    )
    storage = ParquetProfileStorage(tmp_path)

    storage.save([expected])

    assert storage.load() == [expected]
    assert not list(tmp_path.rglob("*.csv"))


def test_empty_storage_round_trip(tmp_path: Path) -> None:
    storage = ParquetProfileStorage(tmp_path)
    storage.save([])
    assert storage.load() == []


def test_numeric_values_and_profile_version_round_trip(tmp_path: Path) -> None:
    from data_profile.models import ColumnProfile, ProfileSlice

    numeric = model("numeric").model_copy(update={
        "columns": [ColumnMetadata(name="amount", data_type="BIGNUMERIC")],
        "profile_version": 2,
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

    storage = ParquetProfileStorage(tmp_path)
    storage.save([numeric])
    loaded = storage.load()[0]

    assert loaded.profile_version == 2
    assert loaded.profiles[0].columns[0].min_value == numeric.profiles[0].columns[0].min_value
    assert loaded.profiles[0].columns[0].max_value == numeric.profiles[0].columns[0].max_value


def test_datetime_and_timestamp_values_round_trip(tmp_path: Path) -> None:
    from data_profile.models import ColumnProfile, ProfileSlice

    values = [
        ("created_at", "DATETIME", "2026-09-09 12:34:56.123456"),
        ("received_at", "TIMESTAMP", "2026-09-09 03:34:56.123456+00"),
    ]
    temporal = model("temporal").model_copy(update={
        "columns": [ColumnMetadata(name=name, data_type=data_type) for name, data_type, _ in values],
        "profile_version": 4,
        "profiles": [ProfileSlice(
            record_count=1,
            columns=[ColumnProfile(
                name=name,
                data_type=data_type,
                null_count=0,
                null_rate=0,
                min_value=value,
                max_value=value,
            ) for name, data_type, value in values],
        )],
    })

    storage = ParquetProfileStorage(tmp_path)
    storage.save([temporal])
    loaded = storage.load()[0]

    assert loaded.profile_version == 4
    assert [(column.data_type, column.min_value, column.max_value)
            for column in loaded.profiles[0].columns] == [
        (data_type, value, value) for _, data_type, value in values
    ]


def test_storage_without_profile_version_loads_as_version_one(tmp_path: Path) -> None:
    import duckdb
    from data_profile.sample import sample_models

    storage = ParquetProfileStorage(tmp_path)
    storage.save(sample_models())
    models_path, _ = storage.paths
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE old_models AS SELECT * EXCLUDE(profile_version) FROM read_parquet(?)",
            [str(models_path)],
        )
        connection.execute("COPY old_models TO ? (FORMAT PARQUET)", [str(models_path)])

    assert storage.load()[0].profile_version == 1


def test_storage_without_lineage_loads_with_empty_upstream_ids(tmp_path: Path) -> None:
    import duckdb

    storage = ParquetProfileStorage(tmp_path)
    storage.save([model("without lineage")])
    models_path, _ = storage.paths
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE old_models AS SELECT * EXCLUDE(upstream_ids_json) FROM read_parquet(?)",
            [str(models_path)],
        )
        connection.execute("COPY old_models TO ? (FORMAT PARQUET)", [str(models_path)])

    assert storage.load()[0].upstream_ids == []


def test_lineage_round_trip(tmp_path: Path) -> None:
    storage = ParquetProfileStorage(tmp_path)
    expected = model("with lineage").model_copy(
        update={"upstream_ids": ["source.test.raw.events"]},
    )

    storage.save([expected])

    assert storage.load()[0].upstream_ids == ["source.test.raw.events"]


def test_distinct_ratio_round_trip_and_legacy_fallback(tmp_path: Path) -> None:
    import duckdb
    from data_profile.sample import sample_models

    storage = ParquetProfileStorage(tmp_path)
    storage.save(sample_models())
    _, profiles_path = storage.paths
    loaded = storage.load()[0]
    category = loaded.profiles[0].columns[0]
    assert category.distinct_count == 2
    assert category.distinct_ratio == 0.2

    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE old_profiles AS SELECT * EXCLUDE(distinct_ratio) FROM read_parquet(?)",
            [str(profiles_path)],
        )
        connection.execute("COPY old_profiles TO ? (FORMAT PARQUET)", [str(profiles_path)])

    legacy_category = storage.load()[0].profiles[0].columns[0]
    assert legacy_category.distinct_ratio == 0.2


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
        connection.execute(
            "INSERT INTO models SELECT * REPLACE ('source.demo.duplicate.events' AS unique_id) "
            "FROM models WHERE model_name = 'events'"
        )
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
