"""Resolve the installed Madako distribution version."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("madako")
except PackageNotFoundError:
    # Keep imports working when the source tree is used without installing the package.
    __version__ = "0+unknown"

