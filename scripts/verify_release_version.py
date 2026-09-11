"""Verify that a release tag matches the package version."""

import argparse
import tomllib
from pathlib import Path


def read_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    version = pyproject.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise SystemExit(f"{pyproject_path} does not define a non-empty project.version")
    return version


def verify_release_tag(tag: str, version: str) -> None:
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise SystemExit(
            f"release tag {tag!r} does not match project version {version!r}; "
            f"expected {expected_tag!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", help="GitHub Release tag to validate")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="pyproject.toml containing project.version",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="Optional GitHub Actions output file to receive version=<version>",
    )
    args = parser.parse_args()

    version = read_project_version(args.pyproject)
    verify_release_tag(args.tag, version)
    print(version)

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output_file:
            output_file.write(f"version={version}\n")


if __name__ == "__main__":
    main()
