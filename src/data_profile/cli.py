import argparse
from pathlib import Path

from data_profile.api import DataProfile, ProfilePlan
from data_profile.config import MadakoConfig, load_config
from data_profile.planning import ProfileProgress
from data_profile.progress import format_bytes, make_profile_renderer
from data_profile.storage import ParquetProfileStorage, build_parquet_fixture
from data_profile.sample import sample_models


def _storage(
    config: MadakoConfig,
    directory: Path | None = None,
) -> ParquetProfileStorage:
    return ParquetProfileStorage(
        directory or config.storage_dir,
        use_generations=config.use_generations,
        keep_generations=config.keep_generations,
    )


def _format_bytes(value: int) -> str:
    return format_bytes(value)


def _print_plan(plan: ProfilePlan, *, show_sql: bool = False) -> None:
    for item in plan.items:
        status = "READY" if item.executable else "BLOCKED"
        print(f"[{status}] {item.model.unique_id}")
        print(f"  Relation: {item.model.relation_name}")
        print(f"  Dimension: {item.dimension or 'Overall'}")
        if item.estimated_bytes is None:
            print("  Query cost check: disabled")
        else:
            assert item.max_bytes_billed is not None
            print(f"  Estimated: {_format_bytes(item.estimated_bytes)}")
            print(f"  Maximum billed: {_format_bytes(item.max_bytes_billed)}")
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
    plan = subparsers.add_parser("plan", help="Resolve profiling config and generate SQL")
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
    profile.add_argument("--threads", type=int)
    profile.add_argument("-v", "--verbose", action="store_true", help="Show query-level execution details")
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
        storage = _storage(config, args.storage_dir)
        uvicorn.run(
            create_app(storage),
            host=args.host or config.host,
            port=args.port or config.port,
        )
    elif args.command == "build-sample":
        output_dir = args.output_dir or config.storage_dir
        storage = _storage(config, output_dir)
        models_path, profiles_path = (
            build_parquet_fixture(args.source, output_dir, storage=storage)
            if args.source
            else storage.save(sample_models())
        )
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
    elif args.command == "import-dbt":
        project_dir = args.project_dir or config.dbt_project_dir
        output_dir = args.output_dir or config.storage_dir
        storage = _storage(config, output_dir)
        DataProfile.from_dbt_project(project_dir, output_dir, storage=storage)
        models_path, profiles_path = storage.paths
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
    elif args.command == "plan":
        storage_dir = args.storage_dir or config.storage_dir
        data_profile = DataProfile.from_storage(
            storage_dir,
            storage=_storage(config, storage_dir),
        )
        plan_result = data_profile.plan(
            select=args.select,
            project=args.project or config.bigquery_project,
            location=args.location or config.location,
        )
        _print_plan(plan_result, show_sql=args.show_sql)
    elif args.command == "profile":
        storage_dir = args.storage_dir or config.storage_dir
        renderer = make_profile_renderer(storage_dir, verbose=args.verbose)
        renderer.phase_started("Importing dbt artifacts")
        try:
            data_profile = DataProfile.from_dbt_project(
                config.dbt_project_dir,
                storage_dir,
                storage=_storage(config, storage_dir),
            )
        except Exception as error:
            renderer.phase_failed("Could not import dbt artifacts", error)
            raise
        renderer.phase_completed("Imported dbt artifacts")

        def report_progress(event: ProfileProgress) -> None:
            renderer.event(event)

        renderer.phase_started("Planning queries")
        try:
            plan_result = data_profile.plan(
                select=args.select,
                project=args.project or config.bigquery_project,
                location=args.location or config.location,
                progress=report_progress,
            )
        except Exception as error:
            renderer.phase_failed("Could not plan queries", error)
            raise
        renderer.plan_completed(plan_result)
        renderer.profiling_started(len(plan_result.items))
        result = data_profile.run(
            plan_result,
            progress=report_progress,
            threads=args.threads if args.threads is not None else config.threads,
        )
        if not result.successful:
            raise SystemExit(1)
        renderer.complete(result)
