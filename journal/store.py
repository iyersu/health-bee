"""File-backed SQLite storage. No network, logging, or automatic migrations.

All public functions require an explicit database path. Call init_db first.
Date filters use the entry's own local date: since <= date < until.
Reads return dictionaries, ordered by occurrence instant and then entry ID.
SQLite files are plaintext; disk encryption is a separate protection.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import math
import sqlite3
from typing import Optional, Union


DatabasePath = Union[str, Path]
SCHEMA_VERSION = 2


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
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    columns = [tuple(row)[1:] for row in connection.execute("PRAGMA table_info(entries)")]
    observations = [tuple(row)[1:] for row in connection.execute("PRAGMA table_info(observations)")]
    if version != SCHEMA_VERSION or columns != _COLUMNS or observations != _OBSERVATION_COLUMNS:
        raise SchemaError("Unsupported journal schema; no migration was performed.")


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
            connection.execute("PRAGMA user_version = 2")
        _check_schema(connection)


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
