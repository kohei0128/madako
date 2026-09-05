"""Verify Phase 2 failure and boundary behavior in a temporary BigQuery dataset."""

import argparse
import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

from data_profile import DataProfile
from data_profile.models import ColumnMetadata, ModelProfile, ProfilingConfig
from data_profile.storage import ParquetProfileStorage


DEFAULT_PROJECT = "northern-bliss-362623"
DEFAULT_LOCATION = "asia-northeast1"
MAX_BYTES = 1_000_000_000


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def query(sql: str, *, project: str, location: str) -> None:
    run([
        "bq",
        "query",
        f"--project_id={project}",
        f"--location={location}",
        "--use_legacy_sql=false",
        sql,
    ])


def column(name: str, data_type: str) -> ColumnMetadata:
    return ColumnMetadata(name=name, data_type=data_type)


def model(
    name: str,
    *,
    project: str,
    dataset: str,
    columns: list[ColumnMetadata],
    dimensions: list[str] | None = None,
    max_bytes_billed: int = MAX_BYTES,
) -> ModelProfile:
    quote = chr(96)
    return ModelProfile(
        unique_id=f"model.phase2_e2e.{name}",
        resource_type="model",
        name=name,
        database=project,
        schema_name=dataset,
        relation_name=f"{quote}{project}.{dataset}.{name}{quote}",
        materialization="table",
        columns=columns,
        profiling=ProfilingConfig(
            enabled=True,
            dimensions=dimensions or [],
            max_bytes_billed=max_bytes_billed,
        ),
    )


def storage_hashes(storage_dir: Path) -> tuple[str, str]:
    return tuple(
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in ParquetProfileStorage(storage_dir).paths
    )


def new_app(models: list[ModelProfile], root: Path, name: str) -> DataProfile:
    storage_dir = root / name
    ParquetProfileStorage(storage_dir).save(models)
    return DataProfile.from_storage(storage_dir)


def create_tables(*, project: str, dataset: str, location: str) -> None:
    quote = chr(96)
    relation = f"{quote}{project}.{dataset}"
    metric_columns = ",\n  ".join(
        f"offset AS metric_{index:02d}" for index in range(1, 16)
    )
    query(
        f"""
CREATE TABLE {relation}.boundary_table{quote} AS
SELECT * FROM UNNEST([
  STRUCT(DATE '2026-09-01' AS event_date, DATE '2026-09-01' AS processed_date,
         'alpha' AS category, 1 AS amount, 1.5 AS ratio, TRUE AS active),
  STRUCT(CAST(NULL AS DATE), DATE '2026-09-02', '', 2, 2.5, FALSE),
  STRUCT(DATE '2026-09-02', CAST(NULL AS DATE), CAST(NULL AS STRING),
         CAST(NULL AS INT64), CAST(NULL AS FLOAT64), CAST(NULL AS BOOL)),
  STRUCT(CAST(NULL AS DATE), CAST(NULL AS DATE), 'omega', 4, 4.5, TRUE)
]);

CREATE TABLE {relation}.empty_table{quote} (
  event_date DATE,
  processed_date DATE,
  category STRING,
  amount INT64,
  ratio FLOAT64,
  active BOOL
);

CREATE TABLE {relation}.fail_first{quote} AS SELECT 1 AS id;
CREATE TABLE {relation}.fail_second{quote} AS SELECT 2 AS id;
CREATE TABLE {relation}.fail_third{quote} AS SELECT 3 AS id;

CREATE TABLE {relation}.limit_table{quote} AS
SELECT
  DATE_ADD(DATE '2000-01-01', INTERVAL offset DAY) AS event_date,
  {metric_columns}
FROM UNNEST(GENERATE_ARRAY(0, 6249)) AS offset;
""",
        project=project,
        location=location,
    )


def verify_boundaries(*, project: str, dataset: str, location: str, root: Path) -> None:
    columns = [
        column("event_date", "DATE"),
        column("processed_date", "DATE"),
        column("category", "STRING"),
        column("amount", "INT64"),
        column("ratio", "FLOAT64"),
        column("active", "BOOL"),
    ]
    models = [
        model(
            "boundary_table",
            project=project,
            dataset=dataset,
            columns=columns,
            dimensions=["event_date", "processed_date"],
        ),
        model(
            "empty_table",
            project=project,
            dataset=dataset,
            columns=columns,
            dimensions=["event_date", "processed_date"],
        ),
    ]
    app = new_app(models, root, "boundaries")
    result = app.run(app.plan(project=project, location=location))
    assert result.successful and result.storage_updated
    loaded = {item.name: item for item in app.models()}

    boundary = loaded["boundary_table"]
    overall = [profile for profile in boundary.profiles if profile.dimension_name is None]
    assert len(overall) == 1 and overall[0].record_count == 4
    for dimension in ("event_date", "processed_date"):
        buckets = [
            profile for profile in boundary.profiles
            if profile.dimension_name == dimension
        ]
        assert sum(profile.record_count for profile in buckets) == 4
        null_bucket = next(
            profile for profile in buckets if profile.dimension_value is None
        )
        assert null_bucket.record_count == 2

    empty = loaded["empty_table"]
    assert len(empty.profiles) == 1
    assert empty.profiles[0].dimension_name is None
    assert empty.profiles[0].record_count == 0
    assert all(
        item.null_rate == 0 and item.missing_rate == 0
        for item in empty.profiles[0].columns
    )
    print("[PASS] NULL buckets, empty table, and multiple dimensions")


def verify_skip(*, project: str, dataset: str, location: str, root: Path) -> None:
    models = [
        model(
            "boundary_table",
            project=project,
            dataset=dataset,
            columns=[column("event_date", "DATE"), column("amount", "INT64")],
        ),
        model(
            "limit_table",
            project=project,
            dataset=dataset,
            columns=[column("event_date", "DATE")],
            max_bytes_billed=1,
        ),
    ]
    app = new_app(models, root, "skip")
    before = storage_hashes(app.storage_dir)
    result = app.run(app.plan(project=project, location=location))
    assert not result.successful and not result.storage_updated
    assert all(item.status == "skipped" for item in result.items)
    assert storage_hashes(app.storage_dir) == before
    print("[PASS] all-skip and unchanged storage")


def verify_fail_fast(*, project: str, dataset: str, location: str, root: Path) -> None:
    columns = [column("id", "INT64")]
    models = [
        model(name, project=project, dataset=dataset, columns=columns)
        for name in ("fail_first", "fail_second", "fail_third")
    ]
    app = new_app(models, root, "fail-fast")
    plan = app.plan(project=project, location=location)
    before = storage_hashes(app.storage_dir)
    run(["bq", "rm", "-f", "-t", f"{project}:{dataset}.fail_second"])
    result = app.run(plan)
    assert [item.status for item in result.items] == [
        "succeeded",
        "failed",
        "skipped",
    ]
    assert not result.storage_updated
    assert storage_hashes(app.storage_dir) == before
    print("[PASS] fail-fast and unchanged storage")


def verify_result_limit(*, project: str, dataset: str, location: str, root: Path) -> None:
    columns = [column("event_date", "DATE")]
    columns.extend(
        column(f"metric_{index:02d}", "INT64") for index in range(1, 16)
    )
    limit_model = model(
        "limit_table",
        project=project,
        dataset=dataset,
        columns=columns,
        dimensions=["event_date"],
    )
    app = new_app([limit_model], root, "result-limit")
    before = storage_hashes(app.storage_dir)
    result = app.run(app.plan(project=project, location=location))
    assert [item.status for item in result.items] == ["failed"]
    assert "row limit" in (result.items[0].error or "")
    assert not result.storage_updated
    assert storage_hashes(app.storage_dir) == before
    print("[PASS] 100,000-row result limit and unchanged storage")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--keep-dataset", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"data_profile_phase2_[a-z0-9_]+", args.dataset):
        raise SystemExit(
            "dataset must start with data_profile_phase2_ and contain "
            "lowercase letters, numbers, or underscores"
        )

    dataset_ref = f"{args.project}:{args.dataset}"
    run([
        "bq",
        "mk",
        "--dataset",
        f"--location={args.location}",
        "--default_table_expiration=86400",
        dataset_ref,
    ])
    try:
        create_tables(
            project=args.project,
            dataset=args.dataset,
            location=args.location,
        )
        with tempfile.TemporaryDirectory(prefix="data-profile-phase2-") as temp_dir:
            root = Path(temp_dir)
            verify_boundaries(
                project=args.project,
                dataset=args.dataset,
                location=args.location,
                root=root,
            )
            verify_skip(
                project=args.project,
                dataset=args.dataset,
                location=args.location,
                root=root,
            )
            verify_fail_fast(
                project=args.project,
                dataset=args.dataset,
                location=args.location,
                root=root,
            )
            verify_result_limit(
                project=args.project,
                dataset=args.dataset,
                location=args.location,
                root=root,
            )
    finally:
        if args.keep_dataset:
            print(f"Kept temporary dataset: {dataset_ref}")
        else:
            run(["bq", "rm", "-r", "-f", "-d", dataset_ref])
            print(f"Deleted temporary dataset: {dataset_ref}")


if __name__ == "__main__":
    main()
