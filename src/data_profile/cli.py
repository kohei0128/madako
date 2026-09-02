import argparse
from pathlib import Path

import uvicorn

from data_profile.planning import create_profile_plan, execute_profile_plan
from data_profile.repository import DuckDBProfileRepository
from data_profile.server import create_app
from data_profile.storage import MODELS_FILENAME, PROFILES_FILENAME, build_dbt_artifact_storage, build_parquet_fixture, write_profile_storage


def main() -> None:
    parser = argparse.ArgumentParser(prog="data-profile")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the local API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--storage-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "fixtures" / "parquet",
    )
    build_sample = subparsers.add_parser("build-sample", help="Build sample Parquet files from JSON")
    build_sample.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "fixtures" / "sample_profiles.json",
    )
    build_sample.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "fixtures" / "parquet",
    )
    import_dbt = subparsers.add_parser("import-dbt", help="Import dbt artifacts into Parquet storage")
    import_dbt.add_argument("--project-dir", type=Path, required=True)
    import_dbt.add_argument("--output-dir", type=Path, required=True)
    plan = subparsers.add_parser("plan", help="Resolve profiling config and dry-run generated SQL")
    plan.add_argument("--storage-dir", type=Path, required=True)
    plan.add_argument("--select")
    plan.add_argument("--project")
    plan.add_argument("--location", default="asia-northeast1")
    plan.add_argument("--show-sql", action="store_true")
    profile = subparsers.add_parser("profile", help="Execute configured profiling and update Parquet storage")
    profile.add_argument("--storage-dir", type=Path, required=True)
    profile.add_argument("--select")
    profile.add_argument("--project")
    profile.add_argument("--location", default="asia-northeast1")
    args = parser.parse_args()

    if args.command == "serve":
        repository = DuckDBProfileRepository(
            args.storage_dir / MODELS_FILENAME,
            args.storage_dir / PROFILES_FILENAME,
        )
        uvicorn.run(create_app(repository), host=args.host, port=args.port)
    elif args.command == "build-sample":
        models_path, profiles_path = build_parquet_fixture(args.source, args.output_dir)
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
    elif args.command == "import-dbt":
        models_path, profiles_path = build_dbt_artifact_storage(args.project_dir, args.output_dir)
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
    elif args.command == "plan":
        repository = DuckDBProfileRepository(
            args.storage_dir / MODELS_FILENAME,
            args.storage_dir / PROFILES_FILENAME,
        )
        items = create_profile_plan(
            repository.list_models(),
            selector=args.select,
            project=args.project,
            location=args.location,
        )
        for item in items:
            status = "READY" if item.executable else "BLOCKED"
            print(f"[{status}] {item.model.unique_id}")
            print(f"  Relation: {item.model.relation_name}")
            print(f"  Dimension: {item.dimension or 'Overall'}")
            print(f"  Estimated bytes: {item.estimated_bytes:,}")
            print(f"  Maximum bytes billed: {item.max_bytes_billed:,}")
            if item.skipped_columns:
                print(f"  Skipped unsupported columns: {', '.join(item.skipped_columns)}")
            if args.show_sql:
                print("  SQL:")
                print(item.sql)
    elif args.command == "profile":
        repository = DuckDBProfileRepository(
            args.storage_dir / MODELS_FILENAME,
            args.storage_dir / PROFILES_FILENAME,
        )
        models = repository.list_models()
        items = create_profile_plan(
            models,
            selector=args.select,
            project=args.project,
            location=args.location,
        )
        for item in items:
            status = "READY" if item.executable else "BLOCKED"
            print(f"[{status}] {item.model.unique_id} / {item.dimension or 'Overall'}")
            print(f"  Estimated bytes: {item.estimated_bytes:,}")
            print(f"  Maximum bytes billed: {item.max_bytes_billed:,}")
        updated_models = execute_profile_plan(models, items)
        write_profile_storage(updated_models, args.storage_dir)
        print(f"Executed profile items: {len(items):,}")
        print(f"Updated storage: {args.storage_dir}")
