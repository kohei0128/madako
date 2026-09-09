import csv
import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import duckdb

from data_profile.dbt_artifacts import read_dbt_artifacts
from data_profile.exceptions import StorageFormatError, StorageOperationError
from data_profile.models import PROFILE_COMPUTATION_VERSION, ColumnMetadata, ModelProfile
from data_profile.repository import DuckDBProfileRepository, JsonProfileRepository


MODELS_FILENAME = "models.parquet"
PROFILES_FILENAME = "column_profiles.parquet"

# Generation directory settings
USE_GENERATION_DIRS = os.environ.get("DATA_PROFILE_USE_GENERATIONS", "").lower() in ("1", "true", "yes")
GENERATIONS_SUBDIR = "generations"
CURRENT_LINK = "current"
KEEP_GENERATIONS = 3  # Keep latest N generations


class StorageRecoveryError(StorageOperationError):
    """Replacement and rollback failed; recovery files must remain available."""

    def __init__(self, recovery_dir: Path):
        self.recovery_dir = recovery_dir
        super().__init__(f"storage rollback failed; stop readers/writers and recover from {recovery_dir}")


class ProfileStorage(Protocol):
    """Profile storage contract for persistence and querying.

    This protocol defines the public API for profile storage, combining
    persistence operations (save, load) with querying capabilities (get_model).
    """

    @property
    def paths(self) -> tuple[Path, Path]:
        """Return the paths to the stored Parquet files.

        Returns:
            tuple[Path, Path]: (models.parquet path, column_profiles.parquet path)
        """
        ...

    def exists(self) -> bool:
        """Check if profile storage exists and is complete.

        Returns:
            bool: True if both Parquet files exist, False otherwise

        Raises:
            StorageFormatError: If only one of the two files exists
        """
        ...

    def load(self) -> list[ModelProfile]:
        """Load all model profiles from storage.

        Returns:
            list[ModelProfile]: All stored model profiles

        Raises:
            StorageFormatError: If storage format is invalid or unsupported
        """
        ...

    def get_model(self, identifier: str) -> ModelProfile:
        """Get a specific model profile by name or unique_id.

        Args:
            identifier: Model name or unique_id to retrieve

        Returns:
            ModelProfile: The requested model profile

        Raises:
            ProfileNotFoundError: If no model matches the identifier
            AmbiguousProfileError: If multiple models match the name (use unique_id instead)
            StorageFormatError: If storage format is invalid or unsupported
        """
        ...

    def save(self, models: list[ModelProfile]) -> tuple[Path, Path]:
        """Save all model profiles to storage.

        Args:
            models: List of model profiles to save

        Returns:
            tuple[Path, Path]: Paths to the saved Parquet files

        Raises:
            StorageFormatError: If models data is invalid
            StorageOperationError: If save operation fails
            StorageRecoveryError: If save fails and rollback also fails
        """
        ...


class ParquetProfileStorage:
    """Parquet-based profile storage with optional generation directory support.

    Storage modes:
    1. Direct mode (default): Writes models.parquet and column_profiles.parquet
       directly to the storage directory
    2. Generation mode: Writes to versioned subdirectories with atomic symlink
       switching for better concurrency safety

    Generation mode is enabled via environment variable:
        DATA_PROFILE_USE_GENERATIONS=1

    When enabled, directory structure becomes:
        .data-profile/
        ├── current -> generations/gen-0002  (symlink)
        └── generations/
            ├── gen-0001/
            └── gen-0002/
                ├── models.parquet
                └── column_profiles.parquet
    """

    def __init__(self, directory: str | Path, *, use_generations: bool | None = None):
        """Initialize Parquet profile storage.

        Args:
            directory: Root directory for profile storage
            use_generations: Enable generation directory mode. If None, uses
                environment variable DATA_PROFILE_USE_GENERATIONS.
        """
        self.directory = Path(directory)
        self._use_generations = USE_GENERATION_DIRS if use_generations is None else use_generations

    @property
    def paths(self) -> tuple[Path, Path]:
        """Return paths to the current Parquet files.

        In generation mode, follows the 'current' symlink to get the active
        generation's files.
        """
        if self._use_generations:
            current_link = self.directory / CURRENT_LINK
            if current_link.exists() and current_link.is_symlink():
                gen_dir = self.directory / current_link.readlink()
                return gen_dir / MODELS_FILENAME, gen_dir / PROFILES_FILENAME
            # No current generation yet
            return self.directory / MODELS_FILENAME, self.directory / PROFILES_FILENAME
        return self.directory / MODELS_FILENAME, self.directory / PROFILES_FILENAME

    def exists(self) -> bool:
        """Check if profile storage exists and is complete.

        In generation mode, checks for the 'current' symlink and validates
        the target generation has both Parquet files.
        """
        if self._use_generations:
            current_link = self.directory / CURRENT_LINK
            if not current_link.exists():
                return False
            if not current_link.is_symlink():
                raise StorageFormatError(f"{CURRENT_LINK} exists but is not a symlink")

            gen_dir = self.directory / current_link.readlink()
            models_path = gen_dir / MODELS_FILENAME
            profiles_path = gen_dir / PROFILES_FILENAME

            present = [models_path.exists(), profiles_path.exists()]
            if any(present) and not all(present):
                raise StorageFormatError(
                    f"incomplete generation at {gen_dir}: both Parquet files are required"
                )
            return all(present)

        present = [path.exists() for path in self.paths]
        if any(present) and not all(present):
            raise StorageFormatError("incomplete profile storage: both Parquet files are required")
        return all(present)

    def load(self) -> list[ModelProfile]:
        """Load all model profiles from storage.

        In generation mode, loads from the current generation via symlink.
        """
        if self._use_generations:
            return _load_with_generation(self.directory)
        return DuckDBProfileRepository(*self.paths).list_models()

    def get_model(self, identifier: str) -> ModelProfile:
        """Get a specific model profile by name or unique_id.

        In generation mode, loads from the current generation via symlink.
        """
        if self._use_generations:
            current_link = self.directory / CURRENT_LINK
            if not current_link.exists():
                raise StorageFormatError("no current generation")
            gen_dir = self.directory / current_link.readlink()
            models_path = gen_dir / MODELS_FILENAME
            profiles_path = gen_dir / PROFILES_FILENAME
            return DuckDBProfileRepository(models_path, profiles_path).get_model(identifier)
        return DuckDBProfileRepository(*self.paths).get_model(identifier)

    def save(self, models: list[ModelProfile]) -> tuple[Path, Path]:
        """Save all model profiles to storage.

        In generation mode, creates a new generation directory and atomically
        switches the 'current' symlink.
        """
        if self._use_generations:
            return _save_with_generation(models, self.directory)
        return write_profile_storage(models, self.directory)


def build_parquet_fixture(source_path: Path, output_dir: Path) -> tuple[Path, Path]:
    models = JsonProfileRepository(source_path).list_models()
    return write_profile_storage(models, output_dir)


def import_dbt_profiles(project_dir: Path, storage: ProfileStorage) -> tuple[Path, Path]:
    models = read_dbt_artifacts(project_dir)
    if storage.exists():
        existing = {model.unique_id: model for model in storage.load()}
        models = [
            model.model_copy(update={
                "profiles": previous.profiles,
                "profiled_at": previous.profiled_at,
                "profile_version": previous.profile_version,
            })
            if (previous := existing.get(model.unique_id)) and previous.profiles
            and previous.profile_version == PROFILE_COMPUTATION_VERSION
            and previous.profiling_signature() == model.profiling_signature()
            else model
            for model in models
        ]
    return storage.save(models)


def write_profile_storage(models: list[ModelProfile], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    models_path = output_dir / MODELS_FILENAME
    profiles_path = output_dir / PROFILES_FILENAME

    stage_dir = Path(tempfile.mkdtemp(prefix=".profile-stage-", dir=output_dir))
    preserve_recovery = False
    try:
        staged_models, staged_profiles = _write_profile_storage_files(models, stage_dir)
        DuckDBProfileRepository(staged_models, staged_profiles).list_models()
        _replace_storage_files(
            ((staged_models, models_path), (staged_profiles, profiles_path)),
            stage_dir,
        )
    except StorageRecoveryError:
        preserve_recovery = True
        raise
    finally:
        if not preserve_recovery:
            shutil.rmtree(stage_dir)

    return models_path, profiles_path


def _write_profile_storage_files(models: list[ModelProfile], output_dir: Path) -> tuple[Path, Path]:
    """Write models and profiles to Parquet files with schema version 1.

    See config-versioning.md for schema versioning details.

    Raises:
        StorageFormatError: If models contain duplicate unique_id
    """
    models_path = output_dir / MODELS_FILENAME
    profiles_path = output_dir / PROFILES_FILENAME

    model_rows: list[tuple] = []
    profile_rows: list[tuple] = []
    ids = [model.unique_id for model in models]
    if len(ids) != len(set(ids)):
        raise StorageFormatError("duplicate relation unique_id")
    for model in models:
        columns = model.columns or _columns_from_profiles(model)
        model_rows.append((
            1,  # schema_version = 1 (current)
            model.unique_id,
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
            model.profile_version,
            json.dumps(model.upstream_ids),
        ))
        for profile_order, profile in enumerate(model.profiles):
            for column_order, column in enumerate(profile.columns):
                profile_rows.append((
                    1,  # schema_version = 1 (current)
                    model.unique_id,
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
                    column.distinct_ratio,
                    _encode_value(column.min_value),
                    _encode_value(column.max_value),
                    column.true_count,
                ))

    models_csv = output_dir / ".models.csv"
    profiles_csv = output_dir / ".column_profiles.csv"
    null_sentinel = f"__MADAKO_NULL_{uuid4().hex}__"

    try:
        _write_csv_rows(models_csv, model_rows, null_sentinel)
        _write_csv_rows(profiles_csv, profile_rows, null_sentinel)
        with duckdb.connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            connection.execute("""
                CREATE TABLE models (
                    schema_version INTEGER NOT NULL,
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
                    profiled_at VARCHAR,
                    profile_version INTEGER,
                    upstream_ids_json VARCHAR NOT NULL
                )
            """)
            if model_rows:
                connection.execute(
                    f"COPY models FROM ? (FORMAT CSV, NULL '{null_sentinel}')",
                    [str(models_csv)],
                )
            connection.execute("""
                CREATE TABLE column_profiles (
                    schema_version INTEGER NOT NULL,
                    unique_id VARCHAR NOT NULL,
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
                    distinct_ratio DOUBLE,
                    min_value VARCHAR,
                    max_value VARCHAR,
                    true_count BIGINT
                )
            """)
            if profile_rows:
                connection.execute(
                    f"COPY column_profiles FROM ? (FORMAT CSV, NULL '{null_sentinel}')",
                    [str(profiles_csv)],
                )
            connection.execute("COPY models TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(models_path)])
            connection.execute("COPY column_profiles TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(profiles_path)])
            connection.execute("COMMIT")
    finally:
        models_csv.unlink(missing_ok=True)
        profiles_csv.unlink(missing_ok=True)

    return models_path, profiles_path


def _write_csv_rows(path: Path, rows: list[tuple], null_sentinel: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(
            [null_sentinel if value is None else value for value in row]
            for row in rows
        )


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
    except Exception as error:
        failures = []
        backed_up_targets = {target for _, target in backups}
        for backup, target in backups:
            try:
                restore = stage_dir / f"{target.name}.restore"
                shutil.copy2(backup, restore)
                os.replace(restore, target)
            except OSError as recovery_error:
                failures.append(recovery_error)
        for target in replaced:
            if target not in backed_up_targets and target.exists():
                try:
                    target.unlink()
                except OSError as recovery_error:
                    failures.append(recovery_error)
        if failures:
            raise StorageRecoveryError(stage_dir) from error
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


# =============================================================================
# Generation Directory Storage (Experimental)
# =============================================================================


def _read_current_generation(storage_dir: Path) -> int:
    """Read the current generation number from symlink or return 0 if none exists.

    Args:
        storage_dir: Root storage directory

    Returns:
        int: Current generation number (0 if no generations exist yet)
    """
    current_link = storage_dir / CURRENT_LINK
    if not current_link.exists():
        return 0

    if not current_link.is_symlink():
        raise StorageFormatError(f"{CURRENT_LINK} exists but is not a symlink")

    target = current_link.readlink()
    # Expected format: generations/gen-0001
    if not target.name.startswith("gen-"):
        raise StorageFormatError(f"unexpected generation directory name: {target}")

    try:
        return int(target.name[4:])  # Extract number from "gen-NNNN"
    except ValueError as error:
        raise StorageFormatError(f"invalid generation number in {target}") from error


def _save_with_generation(models: list[ModelProfile], storage_dir: Path) -> tuple[Path, Path]:
    """Save profiles using generation directory strategy with atomic symlink switching.

    This provides better concurrency safety:
    - Readers follow the 'current' symlink to a stable generation
    - Writers create a new generation directory and atomically update the symlink
    - Multiple old generations are kept for rollback

    Directory structure:
        storage_dir/
        ├── current -> generations/gen-0002  (symlink)
        ├── generations/
        │   ├── gen-0001/
        │   │   ├── models.parquet
        │   │   └── column_profiles.parquet
        │   └── gen-0002/
        │       ├── models.parquet
        │       └── column_profiles.parquet

    Args:
        models: List of model profiles to save
        storage_dir: Root storage directory

    Returns:
        tuple[Path, Path]: Paths to the saved Parquet files in the new generation

    Raises:
        StorageFormatError: If models data is invalid
        StorageOperationError: If save operation fails
    """
    storage_dir.mkdir(parents=True, exist_ok=True)
    generations_dir = storage_dir / GENERATIONS_SUBDIR
    generations_dir.mkdir(exist_ok=True)

    # 1. Determine next generation number
    current_gen = _read_current_generation(storage_dir)
    next_gen = current_gen + 1

    # 2. Create new generation directory
    gen_dir = generations_dir / f"gen-{next_gen:04d}"
    if gen_dir.exists():
        raise StorageOperationError(
            f"generation directory {gen_dir} already exists; "
            "possible concurrent write or incomplete cleanup"
        )
    gen_dir.mkdir()

    # 3. Write Parquet files to new generation
    try:
        models_path, profiles_path = _write_profile_storage_files(models, gen_dir)

        # 4. Validate written files
        DuckDBProfileRepository(models_path, profiles_path).list_models()

        # 5. Atomically switch symlink to new generation
        current_link = storage_dir / CURRENT_LINK
        temp_link = storage_dir / f".current-{next_gen}"

        # Create temporary symlink pointing to new generation
        temp_link.symlink_to(f"{GENERATIONS_SUBDIR}/gen-{next_gen:04d}")

        # Atomically replace current link (POSIX atomic rename)
        os.replace(temp_link, current_link)

        # 6. Clean up old generations
        _cleanup_old_generations(storage_dir, keep=KEEP_GENERATIONS)

        return models_path, profiles_path

    except Exception:
        # Clean up failed generation directory
        if gen_dir.exists():
            shutil.rmtree(gen_dir)
        raise


def _load_with_generation(storage_dir: Path) -> list[ModelProfile]:
    """Load profiles from the current generation directory.

    Follows the 'current' symlink to find the active generation and loads
    profiles from there. This ensures readers see a consistent snapshot.

    Args:
        storage_dir: Root storage directory

    Returns:
        list[ModelProfile]: Loaded model profiles

    Raises:
        StorageFormatError: If current generation is missing or invalid
    """
    current_link = storage_dir / CURRENT_LINK
    if not current_link.exists():
        raise StorageFormatError(
            f"no current generation; expected symlink at {current_link}"
        )

    if not current_link.is_symlink():
        raise StorageFormatError(f"{CURRENT_LINK} exists but is not a symlink")

    # Resolve symlink to get generation directory
    gen_dir = storage_dir / current_link.readlink()
    if not gen_dir.is_dir():
        raise StorageFormatError(
            f"current generation symlink points to non-existent directory: {gen_dir}"
        )

    models_path = gen_dir / MODELS_FILENAME
    profiles_path = gen_dir / PROFILES_FILENAME

    if not models_path.exists() or not profiles_path.exists():
        raise StorageFormatError(
            f"incomplete generation at {gen_dir}; missing Parquet files"
        )

    return DuckDBProfileRepository(models_path, profiles_path).list_models()


def _cleanup_old_generations(storage_dir: Path, keep: int) -> None:
    """Remove old generation directories, keeping only the latest N.

    Args:
        storage_dir: Root storage directory
        keep: Number of most recent generations to preserve
    """
    generations_dir = storage_dir / GENERATIONS_SUBDIR
    if not generations_dir.is_dir():
        return

    # Find all generation directories
    gen_dirs = [
        d for d in generations_dir.iterdir()
        if d.is_dir() and d.name.startswith("gen-")
    ]

    # Sort by generation number (extracted from name)
    def gen_number(path: Path) -> int:
        try:
            return int(path.name[4:])
        except ValueError:
            return 0

    gen_dirs.sort(key=gen_number, reverse=True)

    # Remove old generations beyond the keep limit
    for old_gen in gen_dirs[keep:]:
        try:
            shutil.rmtree(old_gen)
        except OSError:
            # Best effort cleanup; don't fail if we can't remove old generations
            pass
