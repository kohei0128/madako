import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="data-profile")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the local API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run("data_profile.server:app", host=args.host, port=args.port, reload=True)

