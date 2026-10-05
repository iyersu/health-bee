"""Start the local API and browser UI together for personal development."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def _wait_for_session(path: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if process.poll() is not None:
            raise RuntimeError("The local API stopped before it created a session.")
        if time.monotonic() >= deadline:
            raise RuntimeError("The local API did not create a session in time.")
        time.sleep(0.05)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/journal.db"))
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--api-port", type=int, default=8000)
    args = parser.parse_args(argv)
    npm = shutil.which("npm")
    if npm is None:
        parser.exit(1, "npm is required. Install Node.js, then run npm install.\n")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="health-bee-runtime-") as directory:
        runtime = Path(directory)
        os.chmod(runtime, 0o700)
        session_file = runtime / "session.json"
        command = [sys.executable, "-m", "journal.serve", "--db", str(args.db),
                   "--port", str(args.api_port), "--session-file", str(session_file)]
        if args.init_db:
            command.append("--init-db")
        api = subprocess.Popen(command, cwd=root)
        ui = None
        try:
            _wait_for_session(session_file, api)
            environment = os.environ.copy()
            environment["HEALTH_BEE_SESSION_FILE"] = str(session_file)
            print("Health-bee UI: http://127.0.0.1:5173", flush=True)
            ui = subprocess.Popen([npm, "run", "dev"], cwd=root, env=environment)
            return ui.wait()
        except KeyboardInterrupt:
            return 130
        finally:
            for process in (ui, api):
                if process is not None and process.poll() is None:
                    process.terminate()
            for process in (ui, api):
                if process is not None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
