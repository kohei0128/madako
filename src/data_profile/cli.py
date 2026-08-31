import argparse
from pathlib import Path

import uvicorn

from data_profile.repository import DuckDBProfileRepository
from data_profile.server import create_app
from data_profile.storage import MODELS_FILENAME, PROFILES_FILENAME, build_dbt_artifact_storage, build_parquet_fixture


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
