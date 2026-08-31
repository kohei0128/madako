import argparse
from pathlib import Path

import uvicorn

from data_profile.storage import build_parquet_fixture


def main() -> None:
    parser = argparse.ArgumentParser(prog="data-profile")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the local API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
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
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run("data_profile.server:app", host=args.host, port=args.port, reload=True)
    elif args.command == "build-sample":
        models_path, profiles_path = build_parquet_fixture(args.source, args.output_dir)
        print(f"Wrote {models_path}")
        print(f"Wrote {profiles_path}")
