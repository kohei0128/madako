"""Tests for generation directory storage strategy."""

import os
from pathlib import Path

import pytest

from data_profile.models import ColumnMetadata, ModelProfile, ProfilingConfig
from data_profile.storage import ParquetProfileStorage, StorageFormatError


@pytest.fixture
def sample_model():
    return ModelProfile(
        unique_id="model.test.sample",
        resource_type="model",
        name="sample",
        database="test_db",
        schema_name="test_schema",
        materialization="table",
        columns=[ColumnMetadata(name="id", data_type="INT64")],
        profiling=ProfilingConfig(enabled=True),
    )


def test_generation_storage_creates_versioned_directories(tmp_path, sample_model):
    """Test that generation storage creates versioned subdirectories."""
    # Create new storage with generation mode enabled
    storage = ParquetProfileStorage(tmp_path, use_generations=True)
    assert storage._use_generations is True

    # Save should create generation directory structure
    models_path, profiles_path = storage.save([sample_model])

    # Check directory structure
    assert (tmp_path / "current").is_symlink()
    assert (tmp_path / "generations").is_dir()
    assert (tmp_path / "generations" / "gen-0001").is_dir()
    assert models_path == tmp_path / "generations" / "gen-0001" / "models.parquet"
    assert profiles_path == tmp_path / "generations" / "gen-0001" / "column_profiles.parquet"

    # Verify symlink points to first generation
    current_target = (tmp_path / "current").readlink()
    assert current_target == Path("generations/gen-0001")


def test_generation_storage_increments_version_on_save(tmp_path, sample_model):
    """Test that each save creates a new generation."""
    storage = ParquetProfileStorage(tmp_path, use_generations=True)

    # First save: gen-0001
    storage.save([sample_model])
    assert (tmp_path / "generations" / "gen-0001").exists()
    assert (tmp_path / "current").readlink() == Path("generations/gen-0001")

    # Second save: gen-0002
    updated_model = sample_model.model_copy(update={"description": "updated"})
    storage.save([updated_model])
    assert (tmp_path / "generations" / "gen-0002").exists()
    assert (tmp_path / "current").readlink() == Path("generations/gen-0002")

    # Both generations should exist
    assert (tmp_path / "generations" / "gen-0001").exists()
    assert (tmp_path / "generations" / "gen-0002").exists()


def test_generation_storage_loads_from_current_symlink(tmp_path, sample_model):
    """Test that load follows the current symlink."""
    storage = ParquetProfileStorage(tmp_path, use_generations=True)

    # Save two generations
    storage.save([sample_model])
    updated_model = sample_model.model_copy(update={"description": "updated in gen-2"})
    storage.save([updated_model])

    # Load should return data from current generation (gen-0002)
    loaded = storage.load()
    assert len(loaded) == 1
    assert loaded[0].description == "updated in gen-2"


def test_generation_storage_cleans_up_old_generations(tmp_path, sample_model):
    """Test that old generations are cleaned up after threshold."""
    storage = ParquetProfileStorage(tmp_path, use_generations=True)

    # Create 5 generations (keep=3 by default)
    for i in range(5):
        model = sample_model.model_copy(update={"description": f"gen-{i+1}"})
        storage.save([model])

    # Only latest 3 generations should remain
    generations = list((tmp_path / "generations").iterdir())
    assert len(generations) == 3
    assert (tmp_path / "generations" / "gen-0003").exists()
    assert (tmp_path / "generations" / "gen-0004").exists()
    assert (tmp_path / "generations" / "gen-0005").exists()
    assert not (tmp_path / "generations" / "gen-0001").exists()
    assert not (tmp_path / "generations" / "gen-0002").exists()


def test_generation_storage_exists_checks_symlink(tmp_path, sample_model):
    """Test that exists() correctly checks generation directory structure."""
    storage = ParquetProfileStorage(tmp_path, use_generations=True)

    # Initially no storage
    assert not storage.exists()

    # After save, storage should exist
    storage.save([sample_model])
    assert storage.exists()

    # If symlink is removed, exists should return False
    (tmp_path / "current").unlink()
    assert not storage.exists()


def test_generation_storage_fails_on_invalid_symlink(tmp_path):
    """Test that invalid symlink raises error."""
    storage = ParquetProfileStorage(tmp_path, use_generations=True)

    # Create a regular file instead of symlink
    (tmp_path / "current").touch()

    with pytest.raises(StorageFormatError, match="not a symlink"):
        storage.exists()


def test_direct_mode_still_works_by_default(tmp_path, sample_model):
    """Test that direct mode (without generations) still works."""
    # Default: generation mode disabled
    storage = ParquetProfileStorage(tmp_path)
    assert storage._use_generations is False

    # Save should write directly to storage directory
    models_path, profiles_path = storage.save([sample_model])

    assert models_path == tmp_path / "models.parquet"
    assert profiles_path == tmp_path / "column_profiles.parquet"
    assert not (tmp_path / "current").exists()
    assert not (tmp_path / "generations").exists()

    # Load should work
    loaded = storage.load()
    assert len(loaded) == 1
    assert loaded[0].unique_id == sample_model.unique_id
