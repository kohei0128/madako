from pathlib import Path

import pytest

from data_profile.config import find_config, load_config


def test_defaults_are_relative_to_working_directory(tmp_path: Path) -> None:
    config = load_config(start_dir=tmp_path)

    assert config.source is None
    assert config.dbt_project_dir == tmp_path
    assert config.storage_dir == tmp_path / ".data-profile"
    assert config.bigquery_project is None
    assert config.location == "asia-northeast1"
    assert config.host == "127.0.0.1"
    assert config.port == 8000


def test_discovers_parent_config_and_resolves_relative_paths(tmp_path: Path) -> None:
    config_file = tmp_path / "madako.toml"
    config_file.write_text(
        """
[project]
dbt_project_dir = "dbt"
storage_dir = ".madako/storage"

[bigquery]
project = "billing-project"
location = "US"

[server]
host = "0.0.0.0"
port = 8123
""".strip(),
        encoding="utf-8",
    )
    child = tmp_path / "models" / "mart"
    child.mkdir(parents=True)

    assert find_config(child) == config_file
    config = load_config(start_dir=child)

    assert config.source == config_file
    assert config.dbt_project_dir == tmp_path / "dbt"
    assert config.storage_dir == tmp_path / ".madako" / "storage"
    assert config.bigquery_project == "billing-project"
    assert config.location == "US"
    assert config.host == "0.0.0.0"
    assert config.port == 8123


def test_explicit_missing_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Config file not found"):
        load_config(Path("missing.toml"), start_dir=tmp_path)


@pytest.mark.parametrize(
    "content",
    [
        "unknown = true",
        "[server]\nport = 70000",
        "[bigquery]\nlocation = ''",
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, content: str) -> None:
    config_file = tmp_path / "madako.toml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid config"):
        load_config(config_file, start_dir=tmp_path)
