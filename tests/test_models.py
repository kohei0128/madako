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


def test_where_defaults_to_all_rows_and_trims_outer_whitespace() -> None:
    assert ProfilingConfig().where is None
    assert ProfilingConfig(where=None).where is None
    condition = "category = '  active  '\nOR event_date IS NULL -- include unknown dates"
    assert ProfilingConfig(where=f"  {condition}\n ").where == condition


@pytest.mark.parametrize("value", ["", " \n\t ", 10, False, []])
def test_where_rejects_empty_conditions_and_non_strings(value) -> None:
    with pytest.raises(ValidationError, match="where"):
        ProfilingConfig(where=value)
