import pytest
from pydantic import ValidationError

from madako.models import ProfilingConfig


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10_000_000_000, 10_000_000_000),
        ("10000000000", 10_000_000_000),
        ("10 GB", 10_000_000_000),
        ("8 GiB", 8 * 1024**3),
        ("1.5 MB", 1_500_000),
        (".5 KiB", 512),
    ],
)
def test_max_bytes_billed_accepts_readable_sizes(value: int | str, expected: int) -> None:
    assert ProfilingConfig(max_bytes_billed=value).max_bytes_billed == expected


@pytest.mark.parametrize("value", ["ten GB", "10 XB", "0.1 B"])
def test_max_bytes_billed_rejects_invalid_readable_sizes(value: str) -> None:
    with pytest.raises(ValidationError, match="max_bytes_billed"):
        ProfilingConfig(max_bytes_billed=value)
