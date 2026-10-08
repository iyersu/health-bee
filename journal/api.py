"""Authenticated loopback API. No database creation or migration on import."""

import hmac
import json
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from journal import model, parse, review, store

MAX_BODY_BYTES = 64 * 1024
MAX_TEXT_CHARS = 50_000
_ALLOWED_FIELDS = {
    "raw_text", "occurred_at", "mood", "meds", "food", "tags",
    "sleep_hours", "energy", "bleeding", "observations",
}
_SEARCH_FIELDS = {"query", "since", "until"}
_REVIEW_FIELDS = {"since", "until", "include_summary"}


class LocalAccess:
    """Authorize before parsing, bound streamed bodies, and protect every response.

    CSRF protection uses a non-cookie bearer token, exact Origin checks, Fetch
    Metadata checks, and JSON-only writes. No CORS permissions are granted.
    Native clients may omit Origin; they must still supply the bearer token.
    """

    def __init__(self, app, token, port):
        self.app = app
        self.expected_auth = ("Bearer " + token).encode("ascii")
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() not in (b"cache-control", b"pragma", b"referrer-policy")
                ] + [(b"cache-control", b"no-store"), (b"pragma", b"no-cache"),
                     (b"referrer-policy", b"no-referrer"), (b"x-content-type-options", b"nosniff")]
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, protected_send)

        values = {}
        for key, value in scope["headers"]:
            values.setdefault(key.lower(), []).append(value)
        for key in (b"host", b"origin", b"authorization", b"content-length", b"content-type", b"sec-fetch-site"):
            if len(values.get(key, [])) > 1:
                await reject(400, "Duplicate security header.")
                return

        def header(name):
            return values.get(name, [b""])[0].decode("latin-1")

        host = header(b"host")
        if host not in self.hosts:
            await reject(400, "Invalid host.")
            return
        origin = header(b"origin")
        if b"origin" in values and origin != "http://" + host:
            await reject(403, "Origin not allowed.")
            return
        if header(b"sec-fetch-site") not in ("", "same-origin", "none"):
            await reject(403, "Cross-origin request not allowed.")
            return
        if not (scope["path"] == "/api/health" and scope["method"] == "GET"):
            supplied = values.get(b"authorization", [b""])[0]
            if not hmac.compare_digest(supplied, self.expected_auth):
                await reject(401, "A valid local session token is required.")
                return
        if scope["method"] in ("POST", "PATCH"):
            if header(b"content-type").split(";", 1)[0].strip().lower() != "application/json":
                await reject(415, "Use application/json.")
                return
        length = header(b"content-length")
        if b"content-length" in values:
            if not length.isascii() or not length.isdigit():
                await reject(400, "Invalid content length.")
                return
            if len(length) > 10 or int(length) > MAX_BODY_BYTES:
                await reject(413, "Request body too large.")
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                await reject(413, "Request body too large.")
                return
            if not message.get("more_body", False):
                break
        if scope["method"] in ("POST", "PATCH"):
            try:
                # Strict JSON prevents NaN/Infinity and ambiguous duplicate keys.
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Duplicate key")
                        result[key] = value
                    return result

                def invalid_constant(value):
                    raise ValueError("Non-finite JSON value")

                parsed = json.loads(body, object_pairs_hook=unique_object,
                                    parse_constant=invalid_constant)
                if not isinstance(parsed, dict):
                    raise ValueError("Expected object")
            except (ValueError, UnicodeError, RecursionError):
                await reject(422, "Body must be a valid JSON object with unique keys.")
                return
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        # Unexpected failures never echo or log request data or database paths.
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await protected_send(message)

        try:
            await self.app(scope, replay, tracked_send)
        except Exception:
            if not started:
                await reject(500, "Internal server error.")


def _fields(payload, *, creating):
    if set(payload) - _ALLOWED_FIELDS:
        raise HTTPException(422, "Only editable journal fields are accepted.")
    fields = dict(payload)
    if creating and "raw_text" not in fields:
        raise HTTPException(422, "raw_text is required.")
    if "occurred_at" in fields:
        value = fields["occurred_at"]
        if not isinstance(value, str):
            raise HTTPException(422, "occurred_at must be an ISO timestamp with an offset.")
        try:
            fields["occurred_at"] = datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, "occurred_at must be an ISO timestamp with an offset.") from None
    if "observations" in fields:
        observations = fields["observations"]
        if not isinstance(observations, list) or len(observations) > 100:
            raise HTTPException(422, "observations must be a list of at most 100 items.")
    for key, value in fields.items():
        limit = MAX_TEXT_CHARS if key == "raw_text" else 1000
        if isinstance(value, str) and len(value) > limit:
            raise HTTPException(422, "Text field exceeds its size limit.")
    return fields


def _search_fields(payload):
    if set(payload) - _SEARCH_FIELDS:
        raise HTTPException(422, "Only search text and date filters are accepted.")
    query = payload.get("query", "")
    if not isinstance(query, str) or len(query) > 1000:
        raise HTTPException(422, "Search text must be at most 1000 characters.")
    for name in ("since", "until"):
        if name in payload and payload[name] is not None and not isinstance(payload[name], str):
            raise HTTPException(422, "Date filters must be YYYY-MM-DD strings.")
    return {name: payload.get(name) for name in _SEARCH_FIELDS}


def _review_fields(payload):
    if set(payload) - _REVIEW_FIELDS or set(payload) != {"since", "until", "include_summary"}:
        raise HTTPException(422, "Review requires since, until, and include_summary.")
    if not isinstance(payload["since"], str) or not isinstance(payload["until"], str) or type(payload["include_summary"]) is not bool:
        raise HTTPException(422, "Invalid review options.")
    return payload


def create_app(db_path, token, *, port=8000):
    """Build an app with explicit local configuration; never initialize the DB.

    token is a random URL-safe secret supplied by the launcher. It is never
    served by an endpoint, accepted through URLs/cookies, or written to logs.
    """
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or not all(
            character.isalnum() or character in "_-" for character in token):
        raise ValueError("Use a random URL-safe session token of at least 32 characters.")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535.")
    db_path = Path(db_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False,
                  redirect_slashes=False)
    app.add_middleware(LocalAccess, token=token, port=port)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"detail": "Invalid request parameters or JSON body."}, status_code=422)

    @app.exception_handler(ValueError)
    async def invalid_values(request, error):
        return JSONResponse({"detail": "Invalid journal field values."}, status_code=422)

    @app.exception_handler(store.EntryNotFoundError)
    async def missing_entry(request, error):
        return JSONResponse({"detail": "Entry not found."}, status_code=404)

    @app.exception_handler(store.EntryConflictError)
    async def changed_entry(request, error):
        return JSONResponse({"detail": "This entry changed. Reload it before saving."}, status_code=409)

    @app.exception_handler(store.SchemaError)
    async def wrong_schema(request, error):
        return JSONResponse({"detail": "Incompatible database schema; no migration performed."}, status_code=409)

    @app.exception_handler(store.StorageError)
    async def unavailable_storage(request, error):
        return JSONResponse({"detail": "Journal storage unavailable; the operation could not complete."}, status_code=503)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/model/status")
    def local_model_status():
        try:
            return model.model_status()
        except model.LocalModelError as error:
            return {"status": "unavailable", "detail": str(error)}

    @app.post("/api/entries", status_code=201)
    def add(payload: dict = Body(...)):
        entry_id = store.add_entry(db_path, **_fields(payload, creating=True))
        # Do not perform a fallible second read after a successful commit.
        return {"id": entry_id}

    @app.get("/api/entries")
    def list_entries(since: str = None, until: str = None):
        return store.get_entries(db_path, since, until)

    @app.post("/api/entries/search")
    def search_entries(payload: dict = Body(...)):
        return store.get_entries(db_path, **_search_fields(payload))

    @app.get("/api/entries/{entry_id}")
    def read_entry(entry_id: int):
        if not 1 <= entry_id <= 2**63 - 1:
            raise HTTPException(422, "entry_id is outside its valid range.")
        row = store.get_entry(db_path, entry_id)
        if row is None:
            raise HTTPException(404, "Entry not found.")
        return row

    @app.patch("/api/entries/{entry_id}")
    def edit_entry(entry_id: int, payload: dict = Body(...)):
        if not 1 <= entry_id <= 2**63 - 1:
            raise HTTPException(422, "entry_id is outside its valid range.")
        expected_revision = payload.get("revision")
        fields = _fields({key: value for key, value in payload.items() if key != "revision"}, creating=False)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision <= 0):
            raise HTTPException(422, "revision must be a positive integer.")
        return store.update_entry(db_path, entry_id, expected_revision=expected_revision, **fields)

    @app.post("/api/entries/{entry_id}/parse")
    def parse_saved_entry(entry_id: int, payload: dict = Body(...)):
        """Start one explicit local-only analysis attempt for an already saved entry."""
        if not 1 <= entry_id <= 2**63 - 1:
            raise HTTPException(422, "entry_id is outside its valid range.")
        if payload:
            raise HTTPException(422, "Parsing does not accept browser-supplied options.")
        return parse.parse_entry(db_path, entry_id)

    @app.post("/api/parse-attempts/{attempt_id}/apply")
    def apply_suggestions(attempt_id: int, payload: dict = Body(...)):
        if not 1 <= attempt_id <= 2**63 - 1:
            raise HTTPException(422, "attempt_id is outside its valid range.")
        if set(payload) != {"selections"}:
            raise HTTPException(422, "Suggestion application requires selections only.")
        return store.apply_parse_suggestions(db_path, attempt_id, payload["selections"])

    @app.post("/api/review")
    def weekly_review(payload: dict = Body(...)):
        return review.build_review(db_path, **_review_fields(payload))

    return app
