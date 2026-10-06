"""Offline storage checks. Every database is synthetic and temporary."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from journal import store
from journal import backup as backup_cli

from journal.store import (
    SCHEMA_VERSION, EntryNotFoundError, update_entry, SchemaError, StorageError, add_entry, backup_database,
    get_entries, get_entry, init_db, restore_database, verify_database,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "journal.db"
        init_db(self.path)

    def add(self, text="Synthetic entry", when="2026-09-29T12:00:00-05:00", **fields):
        return add_entry(self.path, text, occurred_at=datetime.fromisoformat(when), **fields)

    def test_round_trip_preserves_text_and_confirmed_fields(self):
        text = "  Synthetic café 🌻\nSecond line\n"
        entry_id = self.add(text, mood="calm", meds="none recorded", food="soup", tags="test")
        row = get_entry(self.path, entry_id)
        self.assertEqual(row["raw_text"], text)
        self.assertEqual([row[k] for k in ("mood", "meds", "food", "tags")],
                         ["calm", "none recorded", "soup", "test"])
        self.assertEqual(row["parsed"], 0)
        self.assertEqual(row["created_at"], row["updated_at"])
        self.assertIsNotNone(datetime.fromisoformat(row["created_at"]).utcoffset())
        self.assertTrue(row["occurred_at"].endswith("-05:00"))

    def test_defaults_and_missing_entry(self):
        self.assertEqual(get_entries(self.path), [])
        self.assertIsNone(get_entry(self.path, 999))
        row = get_entry(self.path, add_entry(self.path, "Synthetic default entry"))
        self.assertEqual(row["parsed"], 0)
        for field in ("mood", "meds", "food", "tags"):
            self.assertIsNone(row[field])
        self.assertIsNotNone(datetime.fromisoformat(row["occurred_at"]).utcoffset())

    def test_survives_new_python_process(self):
        entry_id = self.add("Synthetic persistent entry")
        code = (
            "import json, sys; from journal.store import get_entry; "
            "print(json.dumps(get_entry(sys.argv[1], int(sys.argv[2]))))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.path), str(entry_id)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout)["raw_text"], "Synthetic persistent entry")

    def test_empty_or_invalid_text_is_rejected(self):
        for value in ("", " \n\t", None, 12):
            with self.subTest(value=value), self.assertRaises(ValueError):
                add_entry(self.path, value)
        self.assertEqual(get_entries(self.path), [])

    def test_invalid_fields_and_time_are_rejected(self):
        for fields in ({"mood": []}, {"tags": {}}, {"occurred_at": datetime(2026, 9, 29)},
                       {"occurred_at": "2026-09-29"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                add_entry(self.path, "Synthetic entry", **fields)
        self.assertEqual(get_entries(self.path), [])

    def test_date_boundaries_use_original_local_date(self):
        self.add(when="2026-09-28T23:59:59-05:00")
        start = self.add(when="2026-09-29T00:00:00-05:00")
        late = self.add(when="2026-09-29T23:59:59-05:00")
        end = self.add(when="2026-09-30T00:00:00-05:00")
        rows = get_entries(self.path, "2026-09-29", "2026-09-30")
        self.assertEqual([r["id"] for r in rows], [start, late])
        self.assertEqual(len(get_entries(self.path, until="2026-09-29")), 1)
        self.assertEqual([r["id"] for r in get_entries(self.path, since="2026-09-30")], [end])
        self.assertEqual(get_entries(self.path, "2026-09-29", "2026-09-29"), [])

    def test_order_uses_instants_and_id_for_ties(self):
        later = self.add(when="2026-09-29T09:00:00-07:00")
        earlier = self.add(when="2026-09-29T10:00:00-05:00")
        tied = self.add(when="2026-09-29T10:00:00-05:00")
        self.assertEqual([r["id"] for r in get_entries(self.path)], [earlier, tied, later])

    def test_invalid_date_ranges_and_ids(self):
        for since, until in (("2026-02-30", None), ("2026-9-1", None), (1, None),
                             ("2026-10-01", "2026-09-01"), (None, "bad")):
            with self.subTest(since=since, until=until), self.assertRaises(ValueError):
                get_entries(self.path, since, until)
        for value in (0, -1, True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                get_entry(self.path, value)

    def test_repeated_init_preserves_records_and_version(self):
        entry_id = self.add()
        before = self.path.read_bytes()
        init_db(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNotNone(get_entry(self.path, entry_id))
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        finally:
            connection.close()

    def test_unknown_schema_is_rejected_without_modification(self):
        other = Path(self.temp.name) / "unknown.db"
        connection = sqlite3.connect(other)
        connection.execute("CREATE TABLE legacy (text TEXT)")
        connection.execute("INSERT INTO legacy VALUES ('Synthetic legacy data')")
        connection.commit()
        connection.close()
        before = other.read_bytes()
        for operation in (init_db, get_entries):
            with self.assertRaises(SchemaError):
                operation(other)
            self.assertEqual(other.read_bytes(), before)

    def test_future_version_and_changed_columns_are_rejected(self):
        for sql in ("PRAGMA user_version = 99", "ALTER TABLE entries ADD COLUMN unknown TEXT"):
            with self.subTest(sql=sql):
                path = Path(self.temp.name) / ("future.db" if "PRAGMA" in sql else "changed.db")
                init_db(path)
                connection = sqlite3.connect(path)
                connection.execute(sql)
                connection.close()
                before = path.read_bytes()
                with self.assertRaises(SchemaError):
                    init_db(path)
                with self.assertRaises(SchemaError):
                    add_entry(path, "Synthetic entry")
                self.assertEqual(path.read_bytes(), before)

    def test_missing_database_is_not_silently_created(self):
        missing = Path(self.temp.name) / "missing.db"
        for operation in (lambda: get_entries(missing), lambda: get_entry(missing, 1),
                          lambda: add_entry(missing, "Synthetic entry")):
            with self.assertRaises(StorageError):
                operation()
        self.assertFalse(missing.exists())

    def test_sql_like_text_is_stored_literally(self):
        text = "Synthetic '); DROP TABLE entries; --"
        entry_id = self.add(text)
        self.assertEqual(get_entry(self.path, entry_id)["raw_text"], text)
        self.assertEqual(len(get_entries(self.path)), 1)

    def test_failed_insert_rolls_back_and_reports_error(self):
        self.add("Existing synthetic entry")
        connection = sqlite3.connect(self.path)
        connection.execute("""CREATE TRIGGER fail_save AFTER INSERT ON entries
            BEGIN SELECT RAISE(ABORT, 'Simulated failure'); END""")
        connection.close()
        with self.assertRaises(StorageError):
            self.add("Must not appear as saved")
        self.assertEqual([r["raw_text"] for r in get_entries(self.path)], ["Existing synthetic entry"])


    def test_health_fields_and_repeated_symptoms_round_trip(self):
        entry_id = self.add(sleep_hours=7.5, energy=0, bleeding="none", observations=[
            {"symptom": "headache", "severity": 3, "notes": "Synthetic morning"},
            {"symptom": "headache", "severity": 0},
            {"symptom": "cramps"},
        ])
        row = get_entry(self.path, entry_id)
        self.assertEqual((row["sleep_hours"], row["energy"], row["bleeding"]), (7.5, 0, "none"))
        self.assertEqual([o["severity"] for o in row["observations"]], [3, 0, None])
        self.assertTrue(all(o["source"] == "user" for o in row["observations"]))
        self.assertEqual(get_entries(self.path)[0], row)

    def test_unrecorded_health_fields_remain_unknown(self):
        row = get_entry(self.path, self.add())
        for key in ("sleep_hours", "energy", "bleeding"):
            self.assertIsNone(row[key])
        self.assertEqual(row["observations"], [])
        self.assertEqual(row["revision"], 1)

    def test_partial_edit_preserves_other_fields_and_identity(self):
        entry_id = self.add(mood="calm", sleep_hours=8, observations=[{"symptom": "cramps"}])
        before = get_entry(self.path, entry_id)
        row = update_entry(self.path, entry_id, raw_text="  Synthetic corrected text\n")
        self.assertEqual(row["raw_text"], "  Synthetic corrected text\n")
        for key in ("id", "created_at", "occurred_at", "mood", "sleep_hours", "observations"):
            self.assertEqual(row[key], before[key])
        self.assertGreater(row["updated_at"], before["updated_at"])
        self.assertEqual(row["revision"], 2)
        self.assertEqual(get_entry(self.path, entry_id), row)

    def test_edit_and_observations_persist_in_new_process(self):
        entry_id = self.add()
        edited = update_entry(self.path, entry_id, mood="tired", observations=[
            {"symptom": "Synthetic headache", "severity": 5}])
        code = ("import json,sys; from journal.store import get_entry; "
                "print(json.dumps(get_entry(sys.argv[1], int(sys.argv[2]))))")
        result = subprocess.run([sys.executable, "-c", code, str(self.path), str(entry_id)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, check=True)
        self.assertEqual(json.loads(result.stdout), edited)

    def test_clear_nullable_fields_and_replace_observations(self):
        entry_id = self.add(mood="calm", meds="Synthetic medicine", food="soup", tags="test",
                            sleep_hours=8, energy=4, bleeding="light",
                            observations=[{"symptom": "headache"}, {"symptom": "cramps"}])
        replaced = update_entry(self.path, entry_id, observations=[{"symptom": "fatigue"}])
        self.assertEqual([o["symptom"] for o in replaced["observations"]], ["fatigue"])
        fields = {key: None for key in ("mood", "meds", "food", "tags", "sleep_hours", "energy", "bleeding")}
        cleared = update_entry(self.path, entry_id, observations=[], **fields)
        self.assertEqual(cleared["observations"], [])
        for key in fields:
            self.assertIsNone(cleared[key])
        self.assertEqual(cleared["raw_text"], "Synthetic entry")

    def test_noop_edit_keeps_revision_and_timestamp(self):
        entry_id = self.add(observations=[{"symptom": "headache"}])
        before = get_entry(self.path, entry_id)
        self.assertEqual(update_entry(self.path, entry_id), before)
        self.assertEqual(update_entry(self.path, entry_id, raw_text=before["raw_text"],
                                      observations=[{"symptom": "headache"}]), before)

    def test_occurrence_edit_updates_date_filters(self):
        entry_id = self.add()
        row = update_entry(self.path, entry_id,
                           occurred_at=datetime.fromisoformat("2026-09-30T23:30:00-05:00"))
        self.assertEqual(row["occurrence_date"], "2026-09-30")
        self.assertEqual(row["occurred_at_utc"], "2026-10-01T04:30:00.000000+00:00")
        self.assertEqual(get_entries(self.path, until="2026-09-30"), [])
        self.assertEqual(get_entries(self.path, since="2026-09-30")[0]["id"], entry_id)

    def test_invalid_health_values_never_partially_save(self):
        entry_id = self.add(mood="calm", observations=[{"symptom": "headache"}])
        before = get_entry(self.path, entry_id)
        bad_values = [
            {"sleep_hours": -1}, {"sleep_hours": 25}, {"sleep_hours": True},
            {"sleep_hours": 10 ** 1000}, {"sleep_hours": float("nan")}, {"sleep_hours": float("inf")},
            {"energy": -1}, {"energy": 11}, {"energy": True}, {"energy": 2.5},
            {"bleeding": "unknown-value"}, {"bleeding": []},
            {"observations": "headache"}, {"observations": [None]},
            {"observations": [{}]}, {"observations": [{"symptom": "  "}]},
            {"observations": [{"symptom": "headache", "severity": 11}]},
            {"observations": [{"symptom": "headache", "severity": True}]},
            {"observations": [{"symptom": "headache", "notes": 1}]},
            {"observations": [{"symptom": "headache", "source": "ai"}]},
        ]
        for fields in bad_values:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    update_entry(self.path, entry_id, mood="changed", **fields)
                self.assertEqual(get_entry(self.path, entry_id), before)
                with self.assertRaises(ValueError):
                    self.add(**fields)
                self.assertEqual(len(get_entries(self.path)), 1)

    def test_edit_rejects_missing_ids_and_internal_fields(self):
        with self.assertRaises(EntryNotFoundError):
            update_entry(self.path, 999, mood="calm")
        for entry_id in (0, -1, True, "1"):
            with self.assertRaises(ValueError):
                update_entry(self.path, entry_id, mood="calm")
        entry_id = self.add()
        for changes in ({"parsed": 1}, {"revision": 8}, {"created_at": "bad"},
                        {"raw_text": None}, {"raw_text": "  "}, {"observations": None},
                        {"occurred_at": None}, {"mood": []}, {"made_up": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                update_entry(self.path, entry_id, **changes)

    def test_edits_invalidate_parse_state_and_preserve_confirmed_values(self):
        entry_id = self.add(mood="calm")
        connection = sqlite3.connect(self.path)
        connection.execute("UPDATE entries SET parsed=1 WHERE id=?", (entry_id,))
        connection.commit()
        connection.close()
        row = update_entry(self.path, entry_id, observations=[{"symptom": "headache"}])
        self.assertEqual((row["parsed"], row["revision"], row["mood"]), (0, 2, "calm"))

    def test_failed_observation_write_rolls_back_whole_edit_and_add(self):
        entry_id = self.add(observations=[{"symptom": "original"}])
        before = get_entry(self.path, entry_id)
        connection = sqlite3.connect(self.path)
        connection.execute("""CREATE TRIGGER fail_observation AFTER INSERT ON observations
            WHEN NEW.symptom = 'fail' BEGIN SELECT RAISE(ABORT, 'Simulated failure'); END""")
        connection.close()
        observations = [{"symptom": "first succeeds"}, {"symptom": "fail"}]
        with self.assertRaises(StorageError):
            update_entry(self.path, entry_id, raw_text="Must roll back", observations=observations)
        self.assertEqual(get_entry(self.path, entry_id), before)
        with self.assertRaises(StorageError):
            self.add(observations=observations)
        self.assertEqual(get_entries(self.path), [before])

    def test_old_version_is_preserved_and_never_migrated(self):
        path = Path(self.temp.name) / "v1.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, raw_text TEXT)")
        connection.execute("INSERT INTO entries(raw_text) VALUES ('Synthetic old entry')")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()
        before = path.read_bytes()
        for operation in (lambda: init_db(path), lambda: get_entries(path),
                          lambda: update_entry(path, 1, mood="calm")):
            with self.assertRaises(SchemaError):
                operation()
            self.assertEqual(path.read_bytes(), before)

    def test_missing_observation_table_is_rejected(self):
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE observations")
        connection.close()
        before = self.path.read_bytes()
        with self.assertRaises(SchemaError):
            init_db(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_observations_do_not_leak_between_entries(self):
        first = self.add(observations=[{"symptom": "headache"}])
        second = self.add(observations=[{"symptom": "fatigue"}])
        before = get_entry(self.path, second)
        update_entry(self.path, first, observations=[])
        self.assertEqual(get_entry(self.path, second), before)

    def test_backup_and_restore_survive_simulated_loss(self):
        entry_id = self.add("Synthetic backup entry", observations=[{"symptom": "headache"}])
        backup_dir = Path(self.temp.name) / "encrypted-backups"
        backup_dir.mkdir()
        backup = backup_dir / "journal-backup.db"
        self.assertEqual(backup_database(self.path, backup), {"entries": 1, "observations": 1})
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        lost = self.path.with_name("journal-lost.db")
        self.path.rename(lost)
        self.assertEqual(restore_database(backup, self.path), {"entries": 1, "observations": 1})
        self.assertEqual(get_entry(self.path, entry_id)["raw_text"], "Synthetic backup entry")
        self.assertEqual(verify_database(self.path), {"entries": 1, "observations": 1})

    def test_backup_and_restore_refuse_unintended_replacement(self):
        self.add()
        destination = Path(self.temp.name) / "existing.db"
        destination.write_bytes(b"leave this file alone")
        before = destination.read_bytes()
        with self.assertRaises(StorageError):
            backup_database(self.path, destination)
        with self.assertRaises(StorageError):
            restore_database(self.path, destination)
        self.assertEqual(destination.read_bytes(), before)
        self.assertEqual(restore_database(self.path, destination, replace=True),
                         {"entries": 1, "observations": 0})
        self.assertEqual(get_entries(destination)[0]["raw_text"], "Synthetic entry")

    def test_corrupt_or_incompatible_backup_is_rejected_without_restore(self):
        corrupt = Path(self.temp.name) / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite database")
        output = Path(self.temp.name) / "output.db"
        with self.assertRaises(StorageError):
            verify_database(corrupt)
        with self.assertRaises(StorageError):
            restore_database(corrupt, output)
        self.assertFalse(output.exists())
        legacy = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy)
        connection.execute("CREATE TABLE legacy (text TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaises(SchemaError):
            restore_database(legacy, output)
        self.assertFalse(output.exists())

    def test_backup_command_requires_encryption_acknowledgement(self):
        destination = Path(self.temp.name) / "backup.db"
        with patch("journal.backup.store.backup_database") as create, self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                backup_cli.main(["backup", "--db", str(self.path), "--destination", str(destination)])
        create.assert_not_called()
        with patch("journal.backup.store.backup_database", return_value={"entries": 1, "observations": 0}) as create:
            with redirect_stdout(io.StringIO()):
                backup_cli.main(["backup", "--db", str(self.path), "--destination", str(destination),
                                 "--confirm-encrypted-destination"])
        create.assert_called_once_with(self.path, destination)

    def test_restore_command_requires_exact_replacement_confirmation(self):
        backup = Path(self.temp.name) / "backup.db"
        output = Path(self.temp.name) / "journal.db"
        with patch("journal.backup.store.restore_database") as restore, self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                backup_cli.main(["restore", "--backup", str(backup), "--output", str(output), "--replace",
                                 "--confirm-replace", "not-the-output-path"])
        restore.assert_not_called()


    def test_reads_use_one_snapshot_during_concurrent_edit(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.close()
        for list_read in (False, True):
            with self.subTest(list_read=list_read):
                entry_id = self.add(
                    observations=[{"symptom": "before"}])
                before = get_entry(self.path, entry_id)
                original = store._entry_dict
                edited = False

                def read_with_interleaved_edit(connection, row):
                    nonlocal edited
                    if row is not None and row["id"] == entry_id and not edited:
                        edited = True
                        update_entry(self.path, entry_id, raw_text="After concurrent edit",
                                     observations=[{"symptom": "after"}])
                    return original(connection, row)

                with patch.object(store, "_entry_dict", side_effect=read_with_interleaved_edit):
                    if list_read:
                        result = next(r for r in get_entries(self.path) if r["id"] == entry_id)
                    else:
                        result = get_entry(self.path, entry_id)
                self.assertEqual(result, before)
                self.assertEqual(get_entry(self.path, entry_id)["observations"][0]["symptom"], "after")


if __name__ == "__main__":
    unittest.main()
