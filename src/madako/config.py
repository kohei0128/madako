"""Project-local configuration discovery and validation."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


CONFIG_FILENAME = "madako.toml"


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dbt_project_dir: Path = Path(".")
    storage_dir: Path = Path(".madako")


class BigQueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str | None = Field(default=None, min_length=1)
    location: str = Field(default="asia-northeast1", min_length=1)
    threads: int = Field(default=4, ge=1)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["direct", "generations"] | None = None
    keep_generations: int = Field(default=3, ge=2)


class ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: ProjectConfig = ProjectConfig()
    bigquery: BigQueryConfig = BigQueryConfig()
    server: ServerConfig = ServerConfig()
    storage: StorageConfig = StorageConfig()


@dataclass(frozen=True)
class MadakoConfig:
    source: Path | None
    dbt_project_dir: Path
    storage_dir: Path
    bigquery_project: str | None
    location: str
    threads: int
    host: str
    port: int
    use_generations: bool | None
    keep_generations: int


def find_config(start_dir: Path | None = None) -> Path | None:
    """Find madako.toml in the current directory or one of its parents."""
    start = (start_dir or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(
    config_path: Path | None = None,
    *,
    start_dir: Path | None = None,
) -> MadakoConfig:
    """Load a config file and resolve its paths relative to that file."""
    start = (start_dir or Path.cwd()).resolve()
    if config_path is not None:
        source = config_path.expanduser()
        source = source if source.is_absolute() else start / source
        source = source.resolve()
        if not source.is_file():
            raise ValueError(f"Config file not found: {source}")
    else:
        source = find_config(start)

    try:
        values = ConfigFile.model_validate(
            tomllib.loads(source.read_text(encoding="utf-8")) if source else {}
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        label = source or CONFIG_FILENAME
        raise ValueError(f"Invalid config {label}: {error}") from error

    base_dir = source.parent if source else start

    def resolve(path: Path) -> Path:
        path = path.expanduser()
        return path.resolve() if path.is_absolute() else (base_dir / path).resolve()

    return MadakoConfig(
        source=source,
        dbt_project_dir=resolve(values.project.dbt_project_dir),
        storage_dir=resolve(values.project.storage_dir),
        bigquery_project=values.bigquery.project,
        location=values.bigquery.location,
        threads=values.bigquery.threads,
        host=values.server.host,
        port=values.server.port,
        use_generations=(
            values.storage.mode == "generations"
            if values.storage.mode is not None
            else None
        ),
        keep_generations=values.storage.keep_generations,
    )
