import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Protocol

import duckdb

from data_profile.dbt_artifacts import read_dbt_artifacts
from data_profile.models import ColumnMetadata, ModelProfile
from data_profile.repository import DuckDBProfileRepository, JsonProfileRepository


MODELS_FILENAME = "models.parquet"
PROFILES_FILENAME = "column_profiles.parquet"


class ProfileStorage(Protocol):
    """Local storage contract; returned paths identify the persisted files."""

    @property
    def paths(self) -> tuple[Path, Path]: ...

    def exists(self) -> bool: ...

    def load(self) -> list[ModelProfile]: ...

    def save(self, models: list[ModelProfile]) -> tuple[Path, Path]: ...


class ParquetProfileStorage:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    @property
    def paths(self) -> tuple[Path, Path]:
        return self.directory / MODELS_FILENAME, self.directory / PROFILES_FILENAME

    def exists(self) -> bool:
        present = [path.exists() for path in self.paths]
        if any(present) and not all(present):
            raise ValueError("incomplete profile storage: both Parquet files are required")
        return all(present)

    def load(self) -> list[ModelProfile]:
        return DuckDBProfileRepository(*self.paths).list_models()

    def save(self, models: list[ModelProfile]) -> tuple[Path, Path]:
        return write_profile_storage(models, self.directory)


def build_parquet_fixture(source_path: Path, output_dir: Path) -> tuple[Path, Path]:
    models = JsonProfileRepository(source_path).list_models()
    return write_profile_storage(models, output_dir)


def build_dbt_artifact_storage(project_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    return import_dbt_profiles(project_dir, ParquetProfileStorage(output_dir))


def import_dbt_profiles(project_dir: Path, storage: ProfileStorage) -> tuple[Path, Path]:
    models = read_dbt_artifacts(project_dir)
    if storage.exists():
        existing = {model.unique_id: model for model in storage.load()}
        models = [
            model.model_copy(update={"profiles": previous.profiles, "profiled_at": previous.profiled_at})
            if (previous := existing.get(model.unique_id)) and previous.profiles
            else model
            for model in models
        ]
    return storage.save(models)


def write_profile_storage(models: list[ModelProfile], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    models_path = output_dir / MODELS_FILENAME
    profiles_path = output_dir / PROFILES_FILENAME

    with tempfile.TemporaryDirectory(prefix=".profile-stage-", dir=output_dir) as temporary:
        stage_dir = Path(temporary)
        staged_models, staged_profiles = _write_profile_storage_files(models, stage_dir)
        DuckDBProfileRepository(staged_models, staged_profiles).list_models()
        _replace_storage_files(
            ((staged_models, models_path), (staged_profiles, profiles_path)),
            stage_dir,
        )

    return models_path, profiles_path


def _write_profile_storage_files(models: list[ModelProfile], output_dir: Path) -> tuple[Path, Path]:
    models_path = output_dir / MODELS_FILENAME
    profiles_path = output_dir / PROFILES_FILENAME

    model_rows: list[tuple] = []
    profile_rows: list[tuple] = []
    for model in models:
        columns = model.columns or _columns_from_profiles(model)
        model_rows.append((
            model.unique_id or f"{model.resource_type}.{model.name}",
            model.resource_type,
            model.name,
            model.database,
            model.schema_name,
            model.relation_name,
            model.description,
            model.materialization,
            json.dumps(model.tags),
            json.dumps(model.tests),
            json.dumps([column.model_dump() for column in columns]),
            model.profiling.model_dump_json(),
            model.profiled_at.isoformat() if model.profiled_at else None,
        ))
        for profile_order, profile in enumerate(model.profiles):
            for column_order, column in enumerate(profile.columns):
                profile_rows.append((
                    model.name,
                    profile_order,
                    profile.dimension_name,
                    profile.dimension_value,
                    profile.record_count,
                    column_order,
                    column.name,
                    column.data_type,
                    column.description,
                    column.null_count,
                    column.null_rate,
                    column.empty_string_count,
                    column.missing_count,
                    column.missing_rate,
                    column.distinct_count,
                    _encode_value(column.min_value),
                    _encode_value(column.max_value),
                    column.true_count,
                ))

    with duckdb.connect() as connection:
        connection.execute("""
            CREATE TABLE models (
                unique_id VARCHAR NOT NULL,
                resource_type VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                database_name VARCHAR NOT NULL,
                schema_name VARCHAR NOT NULL,
                relation_name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                materialization VARCHAR NOT NULL,
                tags_json VARCHAR NOT NULL,
                tests_json VARCHAR NOT NULL,
                columns_json VARCHAR NOT NULL,
                profiling_json VARCHAR NOT NULL,
                profiled_at VARCHAR
            )
        """)
        connection.executemany("INSERT INTO models VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", model_rows)
        connection.execute("""
            CREATE TABLE column_profiles (
                model_name VARCHAR NOT NULL,
                profile_order INTEGER NOT NULL,
                dimension_name VARCHAR,
                dimension_value VARCHAR,
                record_count BIGINT NOT NULL,
                column_order INTEGER NOT NULL,
                column_name VARCHAR NOT NULL,
                column_type VARCHAR NOT NULL,
                column_description VARCHAR NOT NULL,
                null_count BIGINT NOT NULL,
                null_rate DOUBLE NOT NULL,
                empty_string_count BIGINT NOT NULL,
                missing_count BIGINT NOT NULL,
                missing_rate DOUBLE NOT NULL,
                distinct_count BIGINT,
                min_value VARCHAR,
                max_value VARCHAR,
                true_count BIGINT
            )
        """)
        if profile_rows:
            connection.executemany(
                "INSERT INTO column_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                profile_rows,
            )
        connection.execute("COPY models TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(models_path)])
        connection.execute("COPY column_profiles TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(profiles_path)])

    return models_path, profiles_path


def _replace_storage_files(files: tuple[tuple[Path, Path], ...], stage_dir: Path) -> None:
    backups: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    for _, target in files:
        if target.exists():
            backup = stage_dir / f"{target.name}.backup"
            shutil.copy2(target, backup)
            backups.append((backup, target))

    try:
        for staged, target in files:
            os.replace(staged, target)
            replaced.append(target)
    except Exception:
        backed_up_targets = {target for _, target in backups}
        for backup, target in backups:
            os.replace(backup, target)
        for target in replaced:
            if target not in backed_up_targets and target.exists():
                target.unlink()
        raise


def _columns_from_profiles(model: ModelProfile) -> list[ColumnMetadata]:
    if not model.profiles:
        return []
    return [ColumnMetadata(name=column.name, data_type=column.data_type, description=column.description) for column in model.profiles[0].columns]


def _encode_value(value: str | int | float | bool | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
