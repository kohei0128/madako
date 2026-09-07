"""Small synthetic catalog shipped with the package for the quickstart."""

from datetime import UTC, datetime

from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice


def sample_models() -> list[ModelProfile]:
    def profile(dimension: str | None, value: str | None, count: int) -> ProfileSlice:
        return ProfileSlice(
            dimension_name=dimension, dimension_value=value, record_count=count,
            columns=[
                ColumnProfile(name="category", data_type="STRING", null_count=0,
                              null_rate=0, distinct_count=min(count, 2)),
                ColumnProfile(name="amount", data_type="FLOAT64", null_count=count // 2,
                              null_rate=(count // 2) / count, min_value=1.5, max_value=10.0),
            ],
        )
    events = ModelProfile(
        unique_id="model.demo.events", name="events", database="demo", schema_name="analytics",
        materialization="table", description="Synthetic example data",
        upstream_ids=[
            "source.demo.raw_events",
            "source.demo.raw_users",
            "source.demo.raw_campaigns",
        ],
        columns=[ColumnMetadata(name="category", data_type="STRING"),
                 ColumnMetadata(name="amount", data_type="FLOAT64")],
        profiled_at=datetime(2026, 9, 1, tzinfo=UTC),
        profiles=[profile(None, None, 10),
                  profile("event_date", "2026-08-31", 4),
                  profile("event_date", "2026-09-01", 4),
                  profile("event_date", None, 2),
                  profile("service", "consumer", 4),
                  profile("service", "business", 6)],
    )
    raw_events = ModelProfile(
        unique_id="source.demo.raw_events", resource_type="source", name="raw_events",
        database="demo", schema_name="raw", relation_name="demo.raw.raw_events",
        materialization="source", description="Synthetic upstream source",
        columns=[ColumnMetadata(name="category", data_type="STRING"),
                 ColumnMetadata(name="amount", data_type="FLOAT64")],
    )
    raw_users = ModelProfile(
        unique_id="source.demo.raw_users", resource_type="source", name="raw_users",
        database="demo", schema_name="raw", relation_name="demo.raw.raw_users",
        materialization="source", description="Synthetic upstream user source",
        columns=[ColumnMetadata(name="user_id", data_type="STRING")],
    )
    raw_campaigns = ModelProfile(
        unique_id="source.demo.raw_campaigns", resource_type="source", name="raw_campaigns",
        database="demo", schema_name="raw", relation_name="demo.raw.raw_campaigns",
        materialization="source", description="Synthetic upstream campaign source",
        columns=[ColumnMetadata(name="campaign_id", data_type="STRING")],
    )
    summary = ModelProfile(
        unique_id="model.demo.event_summary", name="summary",
        database="demo", schema_name="analytics", materialization="view",
        description="Synthetic downstream model", upstream_ids=[events.unique_id],
        columns=[ColumnMetadata(name="category", data_type="STRING"),
                 ColumnMetadata(name="total_amount", data_type="FLOAT64")],
    )
    return [events, raw_events, raw_users, raw_campaigns, summary]
