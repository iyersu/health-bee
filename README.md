# Health-bee

A Mac-first, local women's health journal. Day 4 provides a local HTTP API for the completed SQLite storage and editing features. The browser UI starts on Day 5; local AI and the Cycle Journal are later milestones in TODO.md.

## Setup

Use Python 3.12. The pinned dependencies were tested with Python 3.12.14. Apple's system Python 3.9 is not the runtime for this API.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

For running only, install `requirements.txt`. Downloads require internet during setup; running the API and tests afterward does not. On the original development Mac, an isolated `.venv` has already been prepared using the available Python 3.12 runtime. Use `.venv/bin/python` directly there. Other Macs need their own Python 3.12 installation and virtual environment; do not copy `.venv`.

## Run the offline tests

From the repository root:

```bash
.venv/bin/python -m unittest discover -s evals -v
```

Tests use synthetic data and temporary databases. HTTP tests run in-process and do not open a listening socket. The current Starlette release emits a deprecation notice for its supported HTTPX test-client integration; it does not affect the test results.

## Try a separate demo database

```bash
demo_dir=$(mktemp -d)
.venv/bin/python -m journal.serve --db "$demo_dir/journal.db" --init-db
```

This creates a new demo database and starts the API on `http://127.0.0.1:8000`. It also prints the path of a private session file. Leave this Terminal open; stop with Ctrl+C. The demo database persists in that temporary directory until removed by you or the operating system.

In a second Terminal:

```bash
curl http://127.0.0.1:8000/api/health
```

Expected: `{"status":"ok"}`. This is a liveness check, not confirmation that storage is usable.

## Day 6 browser capture

Keep the API terminal running. In a second Terminal, from the repository root, start the Vite UI:

```bash
npm run dev
```

Open `http://127.0.0.1:5173`, choose Settings, and paste the session-file token printed by the API launcher. The token is held in browser memory only. Return to Today, write a synthetic note, and click Save note. History reads saved entries through the local Vite proxy. The UI never calls a hosted service.

### Make an authenticated sample request

From the repository root, run this Python example. Paste the **session file path** printed by the launcher when asked. The script reads the token without printing it or placing it in a command argument or URL.

```bash
.venv/bin/python -c '
import json
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
session = json.loads(Path(input("Local session file path: ").strip()).read_text())
opener = build_opener(ProxyHandler({}))
headers = {"Authorization": "Bearer " + session["token"], "Content-Type": "application/json"}
payload = json.dumps({"raw_text": "Synthetic demo: slept well.", "mood": "calm", "sleep_hours": 8}).encode()
request = Request(session["url"] + "/api/entries", data=payload, headers=headers, method="POST")
with opener.open(request) as response:
    entry_id = json.load(response)["id"]
request = Request(session["url"] + "/api/entries/" + str(entry_id), headers=headers)
with opener.open(request) as response:
    print(json.dumps(json.load(response), indent=2))
'
```

Use synthetic entries in demos and developer tools. Session files grant access to the journal; do not share them. The file is owner-readable/writable only, within an owner-only directory, and is removed on normal shutdown. Restarting generates a new token, invalidating the old one even if an unclean shutdown left its file behind. Browser session bootstrap is future UI work; there is no unauthenticated token endpoint.

## API contract

All journal endpoints require `Authorization: Bearer <session-token>`. Native clients may omit Origin. Browser requests must originate from the exact local host and port they target; cross-origin access is not enabled.

| Method | Path | Success |
| --- | --- | --- |
| GET | `/api/health` | 200, liveness status; no authentication needed |
| POST | `/api/entries` | 201, `{"id": ...}` after a committed save |
| GET | `/api/entries?since=2026-09-01&until=2026-10-01` | 200, entry list |
| GET | `/api/entries/{id}` | 200, one entry |
| PATCH | `/api/entries/{id}` | 200, the updated entry |

POST/PATCH require a JSON object and `Content-Type: application/json`. Supported input fields: raw_text, occurred_at (ISO timestamp with offset), mood, meds, food, tags, sleep_hours, energy, bleeding, observations. POST requires raw_text. PATCH preserves omitted fields; null clears nullable scalars, and observations=[] clears symptoms. Observation items accept symptom, severity, and notes. Database-managed fields such as revision, parsed, and source cannot be set by API clients.

The current `bleeding` field is the stored flow value; the upcoming UI section will be called **Menstrual Period**. Flow alone does not confirm a period or cycle phase. No storage-schema changes were made on Day 4.

Date filters use each entry's local occurrence date, with an inclusive start and exclusive end. Bodies are limited to 64 KiB, raw_text to 50,000 characters, other top-level text fields to 1,000 characters, and symptom lists to 100 items. Dates and observation values also pass storage validation. JSON must have unique keys and finite numbers.

Errors: 400 invalid host/headers; 401 missing/invalid token; 403 rejected browser origin; 404 missing entry; 409 incompatible schema; 413 oversized body; 415 unsupported content type; 422 invalid input; 503 unavailable storage. Error responses omit journal text, tokens, exception details, and database paths.

## Local access and database behavior

- The supplied launcher binds only to 127.0.0.1 and ignores forwarded proxy headers. Use it rather than an externally bound Uvicorn command.
- Access logs are disabled. Sensitive responses, including errors, use Cache-Control: no-store.
- CSRF protection combines an explicitly supplied bearer token (no cookie authentication), exact Origin/Host checks, Fetch Metadata checks, and JSON-only writes. No CORS allowances are configured.
- This boundary does not protect against malicious software already running as your OS user, which can read your database/session files. SQLite remains plaintext; disk protection is a separate requirement.
- A database path is mandatory. App creation/import never initializes or migrates a database. The explicit --init-db flag calls the storage module to create/verify a schema; it never migrates or resets data.
- Existing schema-v1 databases remain incompatible and need a separately approved migration. Schema v2 from Day 3 is unchanged.
- No AI service, remote server, external UI assets, or automatic data uploads are used by the API.
