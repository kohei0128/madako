"""Plan or execute profiling for the tsubo dbt project."""

import argparse
from pathlib import Path

from data_profile import DataProfile


APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DBT_PROJECT = APP_DIR.parents[1] / "dbt" / "tsubo"
DEFAULT_STORAGE_DIR = APP_DIR / "fixtures" / "tsubo"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select")
    parser.add_argument("--project", default="northern-bliss-362623")
    parser.add_argument("--location", default="asia-northeast1")
    parser.add_argument("--dbt-project", type=Path, default=DEFAULT_DBT_PROJECT)
    parser.add_argument("--storage-dir", type=Path, default=DEFAULT_STORAGE_DIR)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute BigQuery profiling and save results; otherwise only dry-run the plan",
    )
    args = parser.parse_args()

    app = DataProfile.from_dbt_project(
        project_dir=args.dbt_project,
        storage_dir=args.storage_dir,
    )
    plan = app.plan(
        select=args.select,
        project=args.project,
        location=args.location,
    )

    for item in plan.items:
        status = "READY" if item.executable else "BLOCKED"
        print(f"[{status}] {item.model.unique_id} / {item.dimension or 'Overall'}")
        print(f"  Relation: {item.model.relation_name}")
        print(f"  Estimated bytes: {item.estimated_bytes:,}")
        print(f"  Maximum bytes billed: {item.max_bytes_billed:,}")

    if not args.execute:
        print("Dry-run only. Add --execute to run profiling and update storage.")
        return

    result = app.run(plan)
    print(f"Profiled models: {', '.join(result.profiled_models)}")
    print(f"Saved models: {result.models_path}")
    print(f"Saved profiles: {result.profiles_path}")


if __name__ == "__main__":
    main()
