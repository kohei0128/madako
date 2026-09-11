"""Verify the public contents of Madako's wheel and source archive."""

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def fail(message: str) -> None:
    raise SystemExit(message)


def single_archive(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        fail(f"expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def sdist_files(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        members = [PurePosixPath(member.name) for member in archive.getmembers() if member.isfile()]
    roots = {member.parts[0] for member in members}
    if len(roots) != 1:
        fail(f"sdist must have one root directory, found: {sorted(roots)}")
    return {str(PurePosixPath(*member.parts[1:])) for member in members}


def wheel_files(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {name for name in archive.namelist() if not name.endswith("/")}


def require(files: set[str], expected: set[str], archive_name: str) -> None:
    missing = sorted(expected - files)
    if missing:
        fail(f"{archive_name} is missing required files: {missing}")


def reject_generated_python(files: set[str], archive_name: str) -> None:
    generated = sorted(
        name for name in files
        if "__pycache__" in PurePosixPath(name).parts or name.endswith((".pyc", ".pyo"))
    )
    if generated:
        fail(f"{archive_name} contains generated Python files: {generated}")


def verify_sdist(path: Path) -> None:
    files = sdist_files(path)
    require(
        files,
        {
            "LICENSE",
            "PKG-INFO",
            "README.md",
            "pyproject.toml",
            "src/madako/__init__.py",
            "src/madako/py.typed",
            "src/madako/web_dist/index.html",
        },
        path.name,
    )
    if not any(name.startswith("src/madako/web_dist/assets/") for name in files):
        fail(f"{path.name} is missing built web assets")
    reject_generated_python(files, path.name)
    allowed_files = {".gitignore", "LICENSE", "PKG-INFO", "README.md", "pyproject.toml"}
    unexpected = sorted(
        name for name in files
        if name not in allowed_files and not name.startswith("src/madako/")
    )
    if unexpected:
        fail(f"{path.name} contains files outside the public allowlist: {unexpected}")
    print(f"Verified {path.name}: {len(files)} files")


def verify_wheel(path: Path) -> None:
    files = wheel_files(path)
    dist_info = {name.split("/", 1)[0] for name in files if ".dist-info/" in name}
    if len(dist_info) != 1:
        fail(f"wheel must have one dist-info directory, found: {sorted(dist_info)}")
    metadata_dir = next(iter(dist_info))
    require(
        files,
        {
            "madako/__init__.py",
            "madako/py.typed",
            "madako/web_dist/index.html",
            f"{metadata_dir}/METADATA",
            f"{metadata_dir}/entry_points.txt",
            f"{metadata_dir}/licenses/LICENSE",
        },
        path.name,
    )
    if not any(name.startswith("madako/web_dist/assets/") for name in files):
        fail(f"{path.name} is missing built web assets")
    reject_generated_python(files, path.name)
    unexpected = sorted(
        name for name in files
        if not name.startswith("madako/") and not name.startswith(f"{metadata_dir}/")
    )
    if unexpected:
        fail(f"{path.name} contains files outside the public allowlist: {unexpected}")
    print(f"Verified {path.name}: {len(files)} files")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: verify_package_contents.py DIST_DIRECTORY")
    directory = Path(sys.argv[1])
    verify_sdist(single_archive(directory, "*.tar.gz"))
    verify_wheel(single_archive(directory, "*.whl"))


if __name__ == "__main__":
    main()
