"""Start Health-bee on loopback with a temporary, owner-only session file."""

import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile

import uvicorn

from journal.api import create_app
from journal import store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Explicit SQLite database path")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--init-db", action="store_true", help="Create/verify schema without migration")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.init_db:
        try:
            store.init_db(args.db)
        except store.StorageError:
            parser.exit(1, "Database initialization failed; no migration was performed.\n")
    elif not args.db.is_file():
        parser.error("database missing; use --init-db to create a new database")
    token = secrets.token_urlsafe(32)
    app = create_app(args.db, token, port=args.port)
    with tempfile.TemporaryDirectory(prefix="health-bee-session-") as directory:
        session_file = Path(directory) / "session.json"
        fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"url": f"http://127.0.0.1:{args.port}", "token": token}, handle)
        print(f"Health-bee API: http://127.0.0.1:{args.port}", flush=True)
        print(f"Local session file: {session_file}", flush=True)
        print("No browser UI yet. Press Ctrl+C to stop.", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                    proxy_headers=False, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
