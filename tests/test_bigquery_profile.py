from data_profile.bigquery_profile import generate_profile_sql, rows_to_profiles
from data_profile.models import ColumnMetadata, ModelProfile


def model() -> ModelProfile:
    return ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        relation_name="`project.dataset.events`",
        materialization="table",
        columns=[
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="category", data_type="STRING"),
            ColumnMetadata(name="amount", data_type="INT64"),
        ],
    )


def test_generates_overall_and_dimension_sql() -> None:
    sql = generate_profile_sql(model(), "event_date")

    assert "FROM `project.dataset.events`" in sql
    assert "GROUP BY `event_date`" in sql
    assert "COUNT(DISTINCT `category`)" in sql
    assert "CAST(MIN(`amount`) AS STRING)" in sql
    assert "UNION ALL" in sql


def test_reconstructs_profile_slices() -> None:
    rows = [
        {
            "dimension_name": None, "dimension_value": None, "record_count": "10",
            "column_order": "2", "column_name": "amount", "column_type": "INT64",
            "null_count": "1", "null_rate": "0.1", "distinct_count": None,
            "min_value": "2", "max_value": "50", "true_count": None,
        },
        {
            "dimension_name": "event_date", "dimension_value": "2026-09-01", "record_count": "4",
            "column_order": "2", "column_name": "amount", "column_type": "INT64",
            "null_count": "0", "null_rate": "0", "distinct_count": None,
            "min_value": "4", "max_value": "30", "true_count": None,
        },
    ]

    profiles = rows_to_profiles(model(), rows)

    assert len(profiles) == 2
    assert profiles[0].dimension_name is None
    assert profiles[0].columns[0].min_value == 2
    assert profiles[1].dimension_value == "2026-09-01"
