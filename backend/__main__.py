"""Start the local API server.

    python -m backend [--port 0] [--workspace DIR] [--ui-dir desktop/dist] [--no-auth] [--exit-on-stdin-eof]

The server only listens on 127.0.0.1. The API token is read from the
``AICS_TOKEN`` environment variable (not a command-line argument, so it does not
show up in process lists). Without a token the server refuses to start unless
``--no-auth`` is given for local development.

When it is ready, the server prints one JSON line to stdout:
``{"event": "ready", "port": 49152, "url": "http://127.0.0.1:49152"}``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings, default_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend", description="AI Circuit Studio local API")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--ui-dir", type=Path, default=None, help="built desktop UI to serve at /")
    parser.add_argument("--no-auth", action="store_true", help="development only: accept requests without a token")
    parser.add_argument("--exit-on-stdin-eof", action="store_true",
                        help="stop when stdin closes (the desktop shell keeps it open)")
    args = parser.parse_args(argv)

    token = os.environ.get("AICS_TOKEN") or None
    if token is None and not args.no_auth:
        print("AICS_TOKEN is not set; pass --no-auth to run without authentication (development only).",
              file=sys.stderr)
        return 2

    settings = Settings(workspace=args.workspace or default_workspace(), token=token, ui_dir=args.ui_dir)
    app = create_app(settings)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(128)  # accept connections into the backlog before uvicorn starts serving
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    if args.exit_on_stdin_eof:
        def watch_stdin() -> None:
            for _ in sys.stdin:
                pass
            server.should_exit = True
        threading.Thread(target=watch_stdin, daemon=True, name="stdin-watch").start()

    print(json.dumps({"event": "ready", "port": port, "url": f"http://127.0.0.1:{port}",
                      "workspace": str(settings.workspace)}), flush=True)
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
