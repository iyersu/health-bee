"""Launch the built Health-bee UI and local API together."""

import argparse
import threading
import webbrowser
from pathlib import Path
import secrets
import uvicorn

from journal import store
from journal.api import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/journal.db"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    static_dir = root / "dist"
    if not (static_dir / "index.html").is_file():
        parser.error("built UI is missing; run npm run build first")
    if args.init_db:
        store.init_db(args.db)
    elif not args.db.is_file():
        parser.error("database missing; use --init-db to create a new database")
    token = secrets.token_urlsafe(32)
    app = create_app(args.db, token, port=args.port, static_dir=static_dir)
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_open:
        threading.Timer(0.35, webbrowser.open, args=(url,)).start()
    print(f"Health-bee: {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                    proxy_headers=False, server_header=False, log_level="warning")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
