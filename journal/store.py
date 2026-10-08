"""File-backed SQLite storage. No network, logging, or automatic migrations.

All public functions require an explicit database path. Call init_db first.
Date filters use the entry's own local date: since <= date < until.
Reads return dictionaries, ordered by occurrence instant and then entry ID.
SQLite files are plaintext; disk encryption is a separate protection.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone
import os
from pathlib import Path
import math
import json
import secrets
import sqlite3
from typing import Optional, Union


DatabasePath = Union[str, Path]
SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = 2


class StorageError(Exception):
    """A database operation failed; the caller must not report a successful save."""


class EntryNotFoundError(StorageError):
    """The requested entry does not exist; nothing was changed."""


class EntryConflictError(StorageError):
    """An entry changed after it was read; the caller must reload before editing."""


class SchemaError(StorageError):
    """The database needs explicit inspection rather than automatic migration."""


_SCHEMA = """
CREATE TABLE entries (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    occurrence_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    mood TEXT,
    meds TEXT,
    food TEXT,
    tags TEXT,
    sleep_hours REAL CHECK (sleep_hours >= 0 AND sleep_hours <= 24),
    energy INTEGER CHECK (energy >= 0 AND energy <= 10),
    bleeding TEXT CHECK (bleeding IN ('none', 'spotting', 'light', 'moderate', 'heavy')),
    revision INTEGER NOT NULL DEFAULT 1,
    parsed INTEGER NOT NULL DEFAULT 0 CHECK (parsed IN (0, 1))
)
"""
_COLUMNS = [
    ("id", "INTEGER", 0, None, 1),
    ("occurred_at", "TEXT", 1, None, 0),
    ("occurred_at_utc", "TEXT", 1, None, 0),
    ("occurrence_date", "TEXT", 1, None, 0),
    ("created_at", "TEXT", 1, None, 0),
    ("updated_at", "TEXT", 1, None, 0),
    ("raw_text", "TEXT", 1, None, 0),
    ("mood", "TEXT", 0, None, 0),
    ("meds", "TEXT", 0, None, 0),
    ("food", "TEXT", 0, None, 0),
    ("tags", "TEXT", 0, None, 0),
    ("sleep_hours", "REAL", 0, None, 0),
    ("energy", "INTEGER", 0, None, 0),
    ("bleeding", "TEXT", 0, None, 0),
    ("revision", "INTEGER", 1, "1", 0),
    ("parsed", "INTEGER", 1, "0", 0),
]


_OBSERVATIONS_SCHEMA = """
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    symptom TEXT NOT NULL,
    severity INTEGER CHECK (severity >= 0 AND severity <= 10),
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'user' CHECK (source = 'user')
)
"""
_OBSERVATION_COLUMNS = [
    ("id", "INTEGER", 0, None, 1),
    ("entry_id", "INTEGER", 1, None, 0),
    ("symptom", "TEXT", 1, None, 0),
    ("severity", "INTEGER", 0, None, 0),
    ("notes", "TEXT", 0, None, 0),
    ("source", "TEXT", 1, "'user'", 0),
]
_PARSE_ATTEMPTS_SCHEMA = """
CREATE TABLE parse_attempts (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    entry_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'unavailable', 'timeout', 'invalid', 'stale')),
    model TEXT NOT NULL,
    model_digest TEXT,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    suggestions_json TEXT
)
"""
_PARSE_ATTEMPT_COLUMNS = [
    ("id", "INTEGER", 0, None, 1),
    ("entry_id", "INTEGER", 1, None, 0),
    ("entry_revision", "INTEGER", 1, None, 0),
    ("status", "TEXT", 1, None, 0),
    ("model", "TEXT", 1, None, 0),
    ("model_digest", "TEXT", 0, None, 0),
    ("prompt_version", "TEXT", 1, None, 0),
    ("created_at", "TEXT", 1, None, 0),
    ("completed_at", "TEXT", 0, None, 0),
    ("suggestions_json", "TEXT", 0, None, 0),
]
_PARSE_FINAL_STATUSES = {"succeeded", "unavailable", "timeout", "invalid", "stale"}
_TEXT_FIELDS = {"mood", "meds", "food", "tags"}
_EDITABLE_FIELDS = _TEXT_FIELDS | {
    "raw_text", "occurred_at", "sleep_hours", "energy", "bleeding", "observations"
}


def _validate_fields(fields):
    if set(fields) - _EDITABLE_FIELDS:
        raise ValueError("Only user-editable journal fields may be changed.")
    for name, value in fields.items():
        if name == "raw_text":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Journal text must be a nonempty string.")
        elif name in _TEXT_FIELDS:
            if value is not None and not isinstance(value, str):
                raise ValueError("Confirmed fields must be strings or None.")
        elif name == "occurred_at":
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("occurred_at must be a timezone-aware datetime.")
        elif name == "sleep_hours":
            if value is not None and (type(value) not in (int, float)
                    or not 0 <= value <= 24 or not math.isfinite(value)):
                raise ValueError("sleep_hours must be a finite number from 0 to 24 or None.")
        elif name == "energy":
            _validate_score(value, "energy")
        elif name == "bleeding":
            if value is not None and (not isinstance(value, str) or value not in
                    ("none", "spotting", "light", "moderate", "heavy")):
                raise ValueError("Invalid bleeding value.")
        elif name == "observations":
            if not isinstance(value, list):
                raise ValueError("observations must be a list; use [] to clear it.")
            for observation in value:
                if not isinstance(observation, dict) or set(observation) - {
                        "symptom", "severity", "notes"}:
                    raise ValueError("Observations accept symptom, severity, and notes only.")
                symptom = observation.get("symptom")
                if not isinstance(symptom, str) or not symptom.strip():
                    raise ValueError("Each observation needs a nonempty symptom.")
                _validate_score(observation.get("severity"), "severity")
                notes = observation.get("notes")
                if notes is not None and not isinstance(notes, str):
                    raise ValueError("Observation notes must be a string or None.")


def _validate_score(value, name):
    if value is not None and (type(value) is not int or not 0 <= value <= 10):
        raise ValueError(name + " must be an integer from 0 to 10 or None.")


def _occurrence_fields(occurrence):
    return {
        "occurred_at": occurrence.isoformat(timespec="microseconds"),
        "occurred_at_utc": occurrence.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        "occurrence_date": occurrence.date().isoformat(),
    }


def _replace_observations(connection, entry_id, observations):
    connection.execute("DELETE FROM observations WHERE entry_id = ?", (entry_id,))
    connection.executemany(
        "INSERT INTO observations (entry_id, symptom, severity, notes) VALUES (?, ?, ?, ?)",
        [(entry_id, item["symptom"], item.get("severity"), item.get("notes"))
         for item in observations],
    )


def _entry_dict(connection, row):
    if row is None:
        return None
    entry = dict(row)
    entry["observations"] = [dict(item) for item in connection.execute(
        "SELECT symptom, severity, notes, source FROM observations WHERE entry_id = ? ORDER BY id",
        (entry["id"],),
    )]
    return entry


def _check_schema(connection):
    """Return the supported schema version without modifying the database."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    columns = [tuple(row)[1:] for row in connection.execute("PRAGMA table_info(entries)")]
    observations = [tuple(row)[1:] for row in connection.execute("PRAGMA table_info(observations)")]
    if columns != _COLUMNS or observations != _OBSERVATION_COLUMNS:
        raise SchemaError("Unsupported journal schema; no migration was performed.")
    if version == LEGACY_SCHEMA_VERSION:
        return version
    attempts = [tuple(row)[1:] for row in connection.execute("PRAGMA table_info(parse_attempts)")]
    if version != SCHEMA_VERSION or attempts != _PARSE_ATTEMPT_COLUMNS:
        raise SchemaError("Unsupported journal schema; no migration was performed.")
    return version


def _require_parse_schema(connection):
    if _check_schema(connection) != SCHEMA_VERSION:
        raise SchemaError("Local AI parsing requires the explicit schema-v3 upgrade.")


@contextmanager
def _connect(path, *, create=False, readonly=False):
    connection = None
    try:
        resolved = Path(path).expanduser().resolve()
        if create:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        mode = "rwc" if create else ("ro" if readonly else "rw")
        connection = sqlite3.connect(resolved.as_uri() + "?mode=" + mode, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            if readonly:
                # Entry fields and observations must come from the same snapshot.
                connection.execute("BEGIN")
            yield connection
    except (sqlite3.Error, OSError) as error:
        raise StorageError("Could not access or save the journal database.") from error
    finally:
        if connection is not None:
            connection.close()


def init_db(path: DatabasePath) -> None:
    """Create an empty database, or verify an existing schema without changing it."""
    with _connect(path, create=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if not objects and version == 0:
            connection.execute(_SCHEMA)
            connection.execute(
                "CREATE INDEX entries_date_idx ON entries(occurrence_date)"
            )
            connection.execute(_OBSERVATIONS_SCHEMA)
            connection.execute("CREATE INDEX observations_entry_idx ON observations(entry_id)")
            connection.execute(_PARSE_ATTEMPTS_SCHEMA)
            connection.execute("CREATE INDEX parse_attempts_entry_idx ON parse_attempts(entry_id)")
            connection.execute("PRAGMA user_version = 3")
        _check_schema(connection)


def migrate_v2_to_v3(path: DatabasePath) -> None:
    """Upgrade a verified schema-v2 journal to v3; callers must seek approval first."""
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _check_schema(connection) != LEGACY_SCHEMA_VERSION:
            raise SchemaError("Only an unchanged schema-v2 journal can be upgraded.")
        connection.execute(_PARSE_ATTEMPTS_SCHEMA)
        connection.execute("CREATE INDEX parse_attempts_entry_idx ON parse_attempts(entry_id)")
        connection.execute("PRAGMA user_version = 3")
        _require_parse_schema(connection)


def add_entry(
    path: DatabasePath,
    raw_text: str,
    *,
    occurred_at: Optional[datetime] = None,
    mood: Optional[str] = None,
    meds: Optional[str] = None,
    food: Optional[str] = None,
    tags: Optional[str] = None,
    sleep_hours: Optional[float] = None,
    energy: Optional[int] = None,
    bleeding: Optional[str] = None,
    observations: Optional[list] = None,
) -> int:
    """Commit an unparsed entry and return its ID. Optional fields are user supplied.

    occurred_at must be timezone-aware; omission uses the current local time.
    Nonempty text is stored verbatim, including leading/trailing whitespace.
    Health field ranges and observation shape are documented in update_entry.
    Input errors raise ValueError; database failures raise StorageError.
    """
    occurrence = occurred_at if occurred_at is not None else datetime.now().astimezone()
    observations = [] if observations is None else observations
    fields = dict(raw_text=raw_text, occurred_at=occurrence, mood=mood, meds=meds,
                  food=food, tags=tags, sleep_hours=sleep_hours, energy=energy,
                  bleeding=bleeding, observations=observations)
    _validate_fields(fields)
    fields.pop("observations")
    fields.update(_occurrence_fields(fields.pop("occurred_at")))
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    fields.update(created_at=now, updated_at=now)
    with _connect(path) as connection:
        _check_schema(connection)
        # Column names come only from the validated field set and internal constants.
        cursor = connection.execute(
            "INSERT INTO entries (" + ", ".join(fields) + ") VALUES ("
            + ", ".join("?" for _ in fields) + ")", tuple(fields.values()),
        )
        entry_id = cursor.lastrowid
        _replace_observations(connection, entry_id, observations)
    return entry_id


def update_entry(path: DatabasePath, entry_id: int, *, expected_revision=None, **changes) -> dict:
    """Atomically edit user-confirmed data and return the persisted entry.

    Omitted fields remain unchanged; None clears nullable scalar fields.
    observations replaces the complete symptom list ([] clears it). Each item
    accepts symptom, optional severity (0–10), and optional notes. Repeated
    symptoms are allowed. Returned source='user' records provenance and is not
    an editable field. Future AI suggestions must use separate storage.

    sleep_hours: 0–24; energy: integer 0–10; bleeding: none/spotting/light/
    moderate/heavy. None means unrecorded, unlike zero or explicit 'none'.
    Actual changes increment revision and reset parsed=0, allowing future AI
    results to be tied to a specific revision. An unchanged update is a no-op.
    A missing entry raises EntryNotFoundError. Version 1 files require a
    separately approved migration; this module never migrates them.
    """
    if type(entry_id) is not int or entry_id <= 0:
        raise ValueError("entry_id must be a positive integer.")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision <= 0):
        raise ValueError("expected_revision must be a positive integer or None.")
    _validate_fields(changes)
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_schema(connection)
        old = _entry_dict(connection, connection.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone())
        if old is None:
            raise EntryNotFoundError("Journal entry does not exist.")
        if expected_revision is not None and old["revision"] != expected_revision:
            raise EntryConflictError("Journal entry changed; reload it before saving.")
        if "occurred_at" in changes:
            changes.update(_occurrence_fields(changes.pop("occurred_at")))
        observations = changes.pop("observations", None)
        observation_change = observations is not None and [
            dict(symptom=o["symptom"], severity=o.get("severity"), notes=o.get("notes"), source="user")
            for o in observations
        ] != old["observations"]
        changes = {key: value for key, value in changes.items() if old[key] != value}
        if changes or observation_change:
            changes.update(updated_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                           revision=old["revision"] + 1, parsed=0)
            connection.execute(
                "UPDATE entries SET " + ", ".join(key + " = ?" for key in changes)
                + " WHERE id = ?", tuple(changes.values()) + (entry_id,),
            )
            if observation_change:
                _replace_observations(connection, entry_id, observations)
        return _entry_dict(connection, connection.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone())


def _parse_attempt_dict(row):
    if row is None:
        return None
    attempt = dict(row)
    encoded = attempt.pop("suggestions_json")
    attempt["suggestions"] = None if encoded is None else json.loads(encoded)
    return attempt


def begin_parse_attempt(path: DatabasePath, entry_id: int, *, model_name: str, prompt_version: str) -> dict:
    """Snapshot a saved entry and record a running analysis attempt before inference."""
    if type(entry_id) is not int or entry_id <= 0:
        raise ValueError("entry_id must be a positive integer.")
    if not isinstance(model_name, str) or not model_name or not isinstance(prompt_version, str) or not prompt_version:
        raise ValueError("model_name and prompt_version must be nonempty strings.")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_parse_schema(connection)
        entry = connection.execute("SELECT id, raw_text, revision FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if entry is None:
            raise EntryNotFoundError("Journal entry does not exist.")
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        cursor = connection.execute(
            "INSERT INTO parse_attempts (entry_id, entry_revision, status, model, prompt_version, created_at) "
            "VALUES (?, ?, 'running', ?, ?, ?)",
            (entry_id, entry["revision"], model_name, prompt_version, now),
        )
        return {"id": cursor.lastrowid, "entry_id": entry_id, "entry_revision": entry["revision"],
                "raw_text": entry["raw_text"]}


def set_parse_model_digest(path: DatabasePath, attempt_id: int, digest: str) -> None:
    """Record the installed local model identity before submitting journal text."""
    if type(attempt_id) is not int or attempt_id <= 0 or not isinstance(digest, str) or not digest:
        raise ValueError("attempt_id and digest must be valid values.")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_parse_schema(connection)
        changed = connection.execute(
            "UPDATE parse_attempts SET model_digest = ? WHERE id = ? AND status = 'running'", (digest, attempt_id)
        ).rowcount
        if changed != 1:
            raise EntryNotFoundError("Running parse attempt does not exist.")


def finish_parse_attempt(path: DatabasePath, attempt_id: int, *, status: str, suggestions=None) -> dict:
    """Finish an attempt without modifying confirmed fields or an edited entry."""
    if type(attempt_id) is not int or attempt_id <= 0 or status not in _PARSE_FINAL_STATUSES:
        raise ValueError("Invalid parse attempt completion.")
    if status == "succeeded" and not isinstance(suggestions, list):
        raise ValueError("Successful attempts require validated suggestions.")
    if status != "succeeded" and suggestions is not None:
        raise ValueError("Only successful attempts may contain suggestions.")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_parse_schema(connection)
        attempt = connection.execute("SELECT * FROM parse_attempts WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None or attempt["status"] != "running":
            raise EntryNotFoundError("Running parse attempt does not exist.")
        if status == "succeeded":
            revision = connection.execute("SELECT revision FROM entries WHERE id = ?", (attempt["entry_id"],)).fetchone()[0]
            if revision != attempt["entry_revision"]:
                status, suggestions = "stale", None
            else:
                connection.execute("UPDATE entries SET parsed = 1 WHERE id = ?", (attempt["entry_id"],))
        completed = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        encoded = None if suggestions is None else json.dumps(suggestions, separators=(",", ":"), ensure_ascii=False)
        connection.execute(
            "UPDATE parse_attempts SET status = ?, completed_at = ?, suggestions_json = ? WHERE id = ?",
            (status, completed, encoded, attempt_id),
        )
        return _parse_attempt_dict(connection.execute("SELECT * FROM parse_attempts WHERE id = ?", (attempt_id,)).fetchone())


def get_parse_attempt(path: DatabasePath, attempt_id: int) -> Optional[dict]:
    """Return metadata and candidate suggestions for one completed or running attempt."""
    if type(attempt_id) is not int or attempt_id <= 0:
        raise ValueError("attempt_id must be a positive integer.")
    with _connect(path, readonly=True) as connection:
        _require_parse_schema(connection)
        return _parse_attempt_dict(connection.execute("SELECT * FROM parse_attempts WHERE id = ?", (attempt_id,)).fetchone())


def apply_parse_suggestions(path: DatabasePath, attempt_id: int, selections: list) -> dict:
    """Atomically confirm selected candidate values for their unchanged source entry.

    selections contain only saved suggestion indexes and user-edited values. An
    empty selection is an explicit rejection of every suggestion and is a no-op.
    """
    if type(attempt_id) is not int or attempt_id <= 0 or not isinstance(selections, list) or len(selections) > 20:
        raise ValueError("Invalid suggestion selection.")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_parse_schema(connection)
        attempt = _parse_attempt_dict(connection.execute("SELECT * FROM parse_attempts WHERE id = ?", (attempt_id,)).fetchone())
        if attempt is None or attempt["status"] != "succeeded" or not isinstance(attempt["suggestions"], list):
            raise EntryNotFoundError("Completed parse attempt does not exist.")
        entry = _entry_dict(connection, connection.execute("SELECT * FROM entries WHERE id = ?", (attempt["entry_id"],)).fetchone())
        if entry is None:
            raise EntryNotFoundError("Journal entry does not exist.")
        if entry["revision"] != attempt["entry_revision"]:
            raise EntryConflictError("Journal entry changed; review suggestions again.")
        chosen, seen_indexes, seen_fields = [], set(), set()
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {"index", "value"}:
                raise ValueError("Each selected suggestion needs an index and value.")
            index = selection["index"]
            if type(index) is not int or not 0 <= index < len(attempt["suggestions"]) or index in seen_indexes:
                raise ValueError("Selected suggestion indexes must be unique and valid.")
            saved = attempt["suggestions"][index]
            field = saved["field"]
            if field != "observations" and field in seen_fields:
                raise ValueError("Choose at most one suggestion for each field.")
            seen_indexes.add(index)
            seen_fields.add(field)
            chosen.append((field, selection["value"]))
        if not chosen:
            return entry
        changes, observations = {}, [
            {
                "symptom": item["symptom"],
                "severity": item.get("severity"),
                "notes": item.get("notes"),
            }
            for item in entry["observations"]
        ]
        original_observations = list(observations)
        for field, value in chosen:
            if field == "observations":
                observations.append(value)
            else:
                changes[field] = value
        if observations != original_observations:
            changes["observations"] = observations
        _validate_fields(changes)
        saved_observations = changes.pop("observations", None)
        scalar_changes = {name: value for name, value in changes.items() if entry[name] != value}
        observation_change = saved_observations is not None and [
            dict(symptom=item["symptom"], severity=item.get("severity"), notes=item.get("notes"), source="user")
            for item in saved_observations
        ] != entry["observations"]
        if scalar_changes or observation_change:
            scalar_changes.update(updated_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                                  revision=entry["revision"] + 1, parsed=0)
            connection.execute("UPDATE entries SET " + ", ".join(name + " = ?" for name in scalar_changes)
                               + " WHERE id = ?", tuple(scalar_changes.values()) + (entry["id"],))
            if observation_change:
                _replace_observations(connection, entry["id"], saved_observations)
        return _entry_dict(connection, connection.execute("SELECT * FROM entries WHERE id = ?", (entry["id"],)).fetchone())


def get_entry(path: DatabasePath, entry_id: int) -> Optional[dict]:
    """Return one entry, or None if its ID does not exist."""
    if type(entry_id) is not int or entry_id <= 0:
        raise ValueError("entry_id must be a positive integer.")
    with _connect(path, readonly=True) as connection:
        _check_schema(connection)
        row = connection.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _entry_dict(connection, row)


def _date_bound(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Date bounds must be YYYY-MM-DD strings.")
    try:
        canonical = date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError("Date bounds must be valid YYYY-MM-DD strings.") from error
    if canonical != value:
        raise ValueError("Date bounds must be YYYY-MM-DD strings.")
    return canonical


def get_entries(
    path: DatabasePath, since: Optional[str] = None, until: Optional[str] = None,
    query: Optional[str] = None,
) -> list:
    """List entries where since <= occurrence_date < until, oldest instant first.

    Bounds are optional ISO calendar dates. query is an optional literal,
    case-insensitive substring of raw_text. Equal bounds return an empty list;
    reversed bounds raise ValueError. Offset changes do not shift an entry's day.
    """
    since, until = _date_bound(since), _date_bound(until)
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be later than until.")
    if query is not None and not isinstance(query, str):
        raise ValueError("Search text must be a string or None.")
    clauses, values = [], []
    if since is not None:
        clauses.append("occurrence_date >= ?")
        values.append(since)
    if until is not None:
        clauses.append("occurrence_date < ?")
        values.append(until)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("raw_text LIKE ? ESCAPE '\\'")
        values.append("%" + escaped + "%")
    query = "SELECT * FROM entries"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY occurred_at_utc, id"
    with _connect(path, readonly=True) as connection:
        _check_schema(connection)
        return [_entry_dict(connection, row) for row in connection.execute(query, values).fetchall()]


def verify_database(path: DatabasePath) -> dict:
    """Read-only integrity, schema, and record-count check for a journal file.

    The returned counts contain no journal text. This never creates, migrates,
    or changes the database.
    """
    with _connect(path, readonly=True) as connection:
        _check_schema(connection)
        result = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if result != ["ok"]:
            raise StorageError("Journal database integrity check failed.")
        return {
            "entries": connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
            "observations": connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
        }


def _existing_path(path: DatabasePath) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise StorageError("Journal database file is unavailable.")
    return resolved


def _destination_path(path: DatabasePath, *, replace: bool) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.parent.is_dir() or resolved.is_dir():
        raise StorageError("Backup destination directory is unavailable.")
    if resolved.exists() and not replace:
        raise StorageError("Backup destination already exists; refusing to overwrite it.")
    if replace and not resolved.exists():
        raise StorageError("Restore destination does not exist; omit replacement confirmation instead.")
    return resolved


def _temporary_database_path(destination: Path) -> Path:
    for _ in range(10):
        temporary = destination.with_name("." + destination.name + "." + secrets.token_hex(12) + ".partial")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            return temporary
        except FileExistsError:
            continue
        except OSError as error:
            raise StorageError("Could not prepare a private temporary database.") from error
    raise StorageError("Could not prepare a unique temporary database.")


def _copy_database(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as source_connection:
            with sqlite3.connect(destination) as destination_connection:
                source_connection.backup(destination_connection)
        os.chmod(destination, 0o600)
    except (sqlite3.Error, OSError) as error:
        raise StorageError("Could not copy the journal database.") from error


def _publish_database(temporary: Path, destination: Path, *, replace: bool) -> None:
    try:
        if replace:
            os.replace(temporary, destination)
        else:
            # link() creates the final name without replacing a concurrently-created file.
            os.link(temporary, destination)
            os.unlink(temporary)
        os.chmod(destination, 0o600)
    except FileExistsError as error:
        raise StorageError("Backup destination already exists; refusing to overwrite it.") from error
    except OSError as error:
        raise StorageError("Could not publish the verified journal database.") from error


def _copy_verified(source: DatabasePath, destination: DatabasePath, *, replace: bool) -> dict:
    source_path = _existing_path(source)
    destination_path = _destination_path(destination, replace=replace)
    if source_path == destination_path:
        raise StorageError("Source and destination must be different journal files.")
    expected = verify_database(source_path)
    temporary = _temporary_database_path(destination_path)
    try:
        _copy_database(source_path, temporary)
        copied = verify_database(temporary)
        if copied != expected:
            raise StorageError("Verified copy does not match the source record counts.")
        _publish_database(temporary, destination_path, replace=replace)
        return copied
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def backup_database(source: DatabasePath, destination: DatabasePath) -> dict:
    """Create a new verified SQLite backup without overwriting an existing file.

    The caller selects and validates an encrypted local destination. The backup
    uses SQLite's backup API, so it is consistent even when the source uses WAL.
    """
    return _copy_verified(source, destination, replace=False)


def restore_database(backup: DatabasePath, destination: DatabasePath, *, replace=False) -> dict:
    """Restore a verified backup into a new file or an explicitly approved replacement.

    A private temporary database is copied and checked before its final name is
    created. replace=True is for an intentional replacement only.
    """
    if type(replace) is not bool:
        raise ValueError("replace must be a boolean.")
    return _copy_verified(backup, destination, replace=replace)
