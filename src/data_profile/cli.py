import argparse
from pathlib import Path

from data_profile.api import DataProfile, ProfilePlan
from data_profile.config import load_config
from data_profile.storage import ParquetProfileStorage, build_parquet_fixture
from data_profile.sample import sample_models


def _print_plan(plan: ProfilePlan, *, show_sql: bool = False) -> None:
    for item in plan.items:
        status = "READY" if item.executable else "BLOCKED"
        print(f"[{status}] {item.model.unique_id}")
        print(f"  Relation: {item.model.relation_name}")
        print(f"  Dimension: {item.dimension or 'Overall'}")
        print(f"  Estimated bytes: {item.estimated_bytes:,}")
        print(f"  Maximum bytes billed: {item.max_bytes_billed:,}")
        if item.skipped_columns:
            print(f"  Skipped unsupported columns: {', '.join(item.skipped_columns)}")
        if show_sql:
            print("  SQL:")
            print(item.sql)


def main() -> None:
    parser = argparse.ArgumentParser(prog="madako")
    parser.add_argument(
        "--config",
        type=Path,
        help="Config file; defaults to madako.toml in the current directory or a parent",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the local Madako app")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--storage-dir", type=Path)
    build_sample = subparsers.add_parser("build-sample", help="Build sample Parquet files from synthetic data or JSON")
    build_sample.add_argument(
        "--source",
        type=Path,
        help="Optional JSON input; defaults to the bundled synthetic example",
    )
    build_sample.add_argument(
        "--output-dir",
        type=Path,
    )
    import_dbt = subparsers.add_parser("import-dbt", help="Import dbt artifacts into Parquet storage")
    import_dbt.add_argument("--project-dir", type=Path)
    import_dbt.add_argument("--output-dir", type=Path)
    plan = subparsers.add_parser("plan", help="Resolve profiling config and dry-run generated SQL")
    plan.add_argument("--storage-dir", type=Path)
    plan.add_argument("--select")
    plan.add_argument("--project")
    plan.add_argument("--location")
    plan.add_argument("--show-sql", action="store_true")
    profile = subparsers.add_parser("profile", help="Execute configured profiling and update Parquet storage")
    profile.add_argument("--storage-dir", type=Path)
    profile.add_argument("--select")
    profile.add_argument("--project")
    profile.add_argument("--location")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ValueError as error:
        parser.error(str(error))

    if config.source:
        print(f"Using config {config.source}")

    if args.command == "serve":
        try:
            import uvicorn

            from data_profile.server import create_app
        except ImportError as error:
            raise SystemExit(
                "Web dependencies are not installed; install madako[web]"
            ) from error
        storage = ParquetProfileStorage(args.storage_dir or config.storage_dir)
        uvicorn.run(
            create_app(storage),
            host=args.host or config.host,
            port=args.port or config.port,
        )
    elif args.command == "build-sample":
        output_dir = args.output_dir or config.storage_dir
        models_path, profiles_path = (
            build_parquet_fixture(args.source, output_dir) if args.source
            else ParquetProfileStorage(output_dir).save(sample_models())
        )
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
    elif args.command == "import-dbt":
        project_dir = args.project_dir or config.dbt_project_dir
        output_dir = args.output_dir or config.storage_dir
        DataProfile.from_dbt_project(project_dir, output_dir)
        models_path, profiles_path = ParquetProfileStorage(output_dir).paths
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
    elif args.command == "plan":
        data_profile = DataProfile.from_storage(args.storage_dir or config.storage_dir)
        plan_result = data_profile.plan(
            select=args.select,
            project=args.project or config.bigquery_project,
            location=args.location or config.location,
        )
        _print_plan(plan_result, show_sql=args.show_sql)
    elif args.command == "profile":
        data_profile = DataProfile.from_storage(args.storage_dir or config.storage_dir)
        plan_result = data_profile.plan(
            select=args.select,
            project=args.project or config.bigquery_project,
            location=args.location or config.location,
        )
        _print_plan(plan_result)
        result = data_profile.run(plan_result)
        for item_result in result.items:
            label = item_result.item.dimension or "Overall"
            detail = f" · {item_result.error}" if item_result.error else f" · {item_result.row_count:,} rows"
            print(f"[{item_result.status.upper()}] {item_result.item.model.name} / {label}{detail}")
        if not result.successful:
            raise SystemExit(1)
        print(f"Profiled models: {', '.join(result.profiled_models)}")
        print(f"Executed profile items: {len(result.plan.items):,}")
        print(f"Updated storage: {data_profile.storage_dir}")
