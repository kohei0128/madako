import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_release_version.py"


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        return tomllib.load(pyproject_file)["project"]["version"]


def test_release_tag_matching_project_version_is_accepted(tmp_path: Path) -> None:
    version = project_version()
    github_output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            f"v{version}",
            "--pyproject",
            str(ROOT / "pyproject.toml"),
            "--github-output",
            str(github_output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == version
    assert github_output.read_text(encoding="utf-8") == f"version={version}\n"


def test_release_tag_not_matching_project_version_is_rejected() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "v999.0.0",
            "--pyproject",
            str(ROOT / "pyproject.toml"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "does not match project version" in result.stderr
