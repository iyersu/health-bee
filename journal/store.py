"""File-backed SQLite storage. No network, logging, or automatic migrations.

All public functions require an explicit database path. Call init_db first.
Date filters use the entry's own local date: since <= date < until.
Reads return dictionaries, ordered by occurrence instant and then entry ID.
SQLite files are plaintext; disk encryption is a separate protection.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
from typing import Optional, Union


DatabasePath = Union[str, Path]
SCHEMA_VERSION = 1


class StorageError(Exception):
    """A database operation failed; the caller must not report a successful save."""


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
    ("parsed", "INTEGER", 1, "0", 0),
]


def _check_schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    columns = [tuple(row)[1:] for row in connection.execute("PRAGMA table_info(entries)")]
    if version != SCHEMA_VERSION or columns != _COLUMNS:
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
        with connection:
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
            connection.execute("PRAGMA user_version = 1")
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
) -> int:
    """Commit an unparsed entry and return its ID. Optional fields are user supplied.

    occurred_at must be timezone-aware; omission uses the current local time.
    Nonempty text is stored verbatim, including leading/trailing whitespace.
    Input errors raise ValueError; database failures raise StorageError.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Journal text must be a nonempty string.")
    for value in (mood, meds, food, tags):
        if value is not None and not isinstance(value, str):
            raise ValueError("Confirmed fields must be strings or None.")
    occurrence = occurred_at if occurred_at is not None else datetime.now().astimezone()
    if not isinstance(occurrence, datetime) or occurrence.utcoffset() is None:
        raise ValueError("occurred_at must be a timezone-aware datetime.")
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    values = (
        occurrence.isoformat(timespec="microseconds"),
        occurrence.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        occurrence.date().isoformat(), now, now, raw_text, mood, meds, food, tags,
    )
    with _connect(path) as connection:
        _check_schema(connection)
        cursor = connection.execute(
            """INSERT INTO entries
            (occurred_at, occurred_at_utc, occurrence_date, created_at, updated_at,
             raw_text, mood, meds, food, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        entry_id = cursor.lastrowid
    return entry_id


def get_entry(path: DatabasePath, entry_id: int) -> Optional[dict]:
    """Return one entry, or None if its ID does not exist."""
    if type(entry_id) is not int or entry_id <= 0:
        raise ValueError("entry_id must be a positive integer.")
    with _connect(path, readonly=True) as connection:
        _check_schema(connection)
        row = connection.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row is not None else None


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
    path: DatabasePath, since: Optional[str] = None, until: Optional[str] = None
) -> list:
    """List entries where since <= occurrence_date < until, oldest instant first.

    Bounds are optional ISO calendar dates. Equal bounds return an empty list;
    reversed bounds raise ValueError. Offset changes do not shift an entry's day.
    """
    since, until = _date_bound(since), _date_bound(until)
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be later than until.")
    clauses, values = [], []
    if since is not None:
        clauses.append("occurrence_date >= ?")
        values.append(since)
    if until is not None:
        clauses.append("occurrence_date < ?")
        values.append(until)
    query = "SELECT * FROM entries"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY occurred_at_utc, id"
    with _connect(path, readonly=True) as connection:
        _check_schema(connection)
        return [dict(row) for row in connection.execute(query, values)]
