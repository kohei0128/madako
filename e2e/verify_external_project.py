"""Smoke-test an installed wheel against artifacts from a separate dbt project."""

import argparse
from pathlib import Path

from data_profile import DataProfile


def profile_rows() -> list[dict]:
    values = [
        ("id", "INT64", "1", "1"),
        ("amount", "NUMERIC", "12.34", "12.34"),
    ]
    return [
        {
            "dimension_name": None,
            "dimension_value": None,
            "record_count": "1",
            "column_order": str(order),
            "column_name": name,
            "column_type": data_type,
            "null_count": "0",
            "null_rate": "0",
            "empty_string_count": "0",
            "missing_count": "0",
            "missing_rate": "0",
            "distinct_count": None,
            "min_value": minimum,
            "max_value": maximum,
            "true_count": None,
        }
        for order, (name, data_type, minimum, maximum) in enumerate(values)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--storage-dir", type=Path, required=True)
    args = parser.parse_args()

    app = DataProfile.from_dbt_project(
        args.project_dir,
        args.storage_dir,
        estimator=lambda *_: 128,
        runner=lambda *_: profile_rows(),
    )
    plan = app.plan(select="model.external_demo.events")
    result = app.run(plan)
    persisted = app.models()[0]

    assert result.successful and result.storage_updated
    assert persisted.unique_id == "model.external_demo.events"
    assert persisted.profiles[0].record_count == 1
    amount = next(column for column in persisted.profiles[0].columns if column.name == "amount")
    assert amount.data_type == "NUMERIC"
    assert amount.min_value == "12.34"
    print("External dbt project smoke test passed")


if __name__ == "__main__":
    main()
