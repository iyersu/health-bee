"""In-process HTTP tests: no listening socket and no personal databases."""

import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from journal import store
from journal.api import MAX_BODY_BYTES, create_app
from journal.serve import main

TOKEN = "synthetic_test_token_" + "a" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        store.init_db(self.db)
        self.app = create_app(self.db, TOKEN)
        self.client = self.enterContext(TestClient(self.app, base_url="http://127.0.0.1:8000"))

    def create(self, **fields):
        payload = {"raw_text": "Synthetic journal", "occurred_at": "2026-09-29T12:00:00-05:00"}
        payload.update(fields)
        return self.client.post("/api/entries", headers=AUTH, json=payload)

    def test_create_read_filter_and_partial_edit(self):
        result = self.create(mood="calm", observations=[{"symptom": "headache", "severity": 3}])
        self.assertEqual(result.status_code, 201)
        entry_id = result.json()["id"]
        before = self.client.get(f"/api/entries/{entry_id}", headers=AUTH).json()
        self.assertEqual(before["observations"][0]["symptom"], "headache")
        listing = self.client.get("/api/entries?since=2026-09-29&until=2026-09-30", headers=AUTH)
        self.assertEqual(listing.json(), [before])
        self.assertEqual(self.client.get("/api/entries?since=2026-09-30", headers=AUTH).json(), [])
        result = self.client.patch(f"/api/entries/{entry_id}", headers=AUTH,
                                   json={"mood": None, "energy": 0, "observations": []})
        self.assertEqual(result.status_code, 200)
        after = result.json()
        self.assertIsNone(after["mood"])
        self.assertEqual(after["raw_text"], before["raw_text"])
        self.assertEqual(after["observations"], [])
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after, store.get_entry(self.db, entry_id))

    def test_private_search_filters_dates_and_escapes_like_characters(self):
        first = self.create(raw_text="Morning walk and tea", occurred_at="2026-09-29T08:00:00-05:00").json()["id"]
        second = self.create(raw_text="Evening walk, 100% complete", occurred_at="2026-09-29T20:00:00-05:00").json()["id"]
        self.create(raw_text="Walk planned tomorrow", occurred_at="2026-09-30T08:00:00-05:00")
        response = self.client.post("/api/entries/search", headers=AUTH,
                                    json={"query": "walk", "since": "2026-09-29", "until": "2026-09-30"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["id"] for entry in response.json()], [first, second])
        literal = self.client.post("/api/entries/search", headers=AUTH, json={"query": "100%"})
        self.assertEqual([entry["id"] for entry in literal.json()], [second])
        none = self.client.post("/api/entries/search", headers=AUTH, json={"query": "not present"})
        self.assertEqual(none.status_code, 200)
        self.assertEqual(none.json(), [])
        self.assertEqual(self.client.post("/api/entries/search", headers=AUTH,
                                          json={"query": "x", "unknown": "x"}).status_code, 422)

    def test_edit_rejects_a_stale_revision_without_overwriting(self):
        entry_id = self.create(raw_text="Original note").json()["id"]
        original = self.client.get(f"/api/entries/{entry_id}", headers=AUTH).json()
        saved = self.client.patch(f"/api/entries/{entry_id}", headers=AUTH,
                                  json={"raw_text": "First correction", "revision": original["revision"]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["revision"], original["revision"] + 1)
        stale = self.client.patch(f"/api/entries/{entry_id}", headers=AUTH,
                                  json={"raw_text": "Stale correction", "revision": original["revision"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(store.get_entry(self.db, entry_id)["raw_text"], "First correction")

    def test_journal_routes_require_token_before_storage_or_parsing(self):
        with patch("journal.api.store.get_entries") as read:
            for headers in ({}, {"Authorization": "Bearer incorrect"}):
                self.assertEqual(self.client.get("/api/entries", headers=headers).status_code, 401)
            read.assert_not_called()
        for method, path in (("post", "/api/entries"), ("get", "/api/entries/1"),
                             ("patch", "/api/entries/1")):
            self.assertEqual(self.client.request(method, path, content="invalid").status_code, 401)
        self.assertEqual(self.client.get("/api/entries?token=" + TOKEN).status_code, 401)
        self.assertEqual(self.client.get("/api/entries", headers={"Cookie": "token=" + TOKEN}).status_code, 401)

    def test_health_returns_no_personal_data(self):
        with patch("journal.api.store.get_entries") as read:
            result = self.client.get("/api/health")
            self.assertEqual(result.json(), {"status": "ok"})
            read.assert_not_called()
        self.assertEqual(self.client.get("/docs", headers=AUTH).status_code, 404)
        self.assertEqual(self.client.get("/openapi.json", headers=AUTH).status_code, 404)

    def test_host_and_origin_validation_even_with_token(self):
        for host in ("evil.example:8000", "127.0.0.1.evil.example:8000", "127.0.0.1:9000"):
            self.assertEqual(self.client.get("/api/entries", headers={**AUTH, "Host": host}).status_code, 400)
        for origin in ("https://evil.example", "null", "", "http://127.0.0.1:9000", "http://localhost:8000"):
            result = self.client.post("/api/entries", headers={**AUTH, "Origin": origin},
                                      json={"raw_text": "Must not save"})
            self.assertEqual(result.status_code, 403)
        result = self.client.get("/api/entries", headers={**AUTH, "Origin": "http://127.0.0.1:8000"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(store.get_entries(self.db), [])

    def test_fetch_metadata_and_preflight_are_rejected(self):
        for site in ("cross-site", "same-site"):
            self.assertEqual(self.client.get("/api/entries", headers={**AUTH, "Sec-Fetch-Site": site}).status_code, 403)
        result = self.client.options("/api/entries", headers={"Origin": "https://evil.example",
                                                             "Access-Control-Request-Method": "POST"})
        self.assertEqual(result.status_code, 403)
        self.assertNotIn("access-control-allow-origin", result.headers)

    def test_duplicate_security_headers_rejected(self):
        result = self.client.get("/api/entries", headers=[("Authorization", "Bearer " + TOKEN),
                                                          ("Authorization", "Bearer " + TOKEN)])
        self.assertEqual(result.status_code, 400)

    def test_json_only_writes_and_strict_json(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            result = self.client.post("/api/entries", headers={**AUTH, "Content-Type": content_type},
                                      content='{"raw_text":"Must not save"}')
            self.assertEqual(result.status_code, 415)
        for body in ('{"raw_text":', '[]', 'null', '{"raw_text":"a","raw_text":"b"}',
                     '{"raw_text":"test","sleep_hours":NaN}'):
            result = self.client.post("/api/entries", headers={**AUTH, "Content-Type": "application/json"}, content=body)
            self.assertEqual(result.status_code, 422)
        self.assertEqual(store.get_entries(self.db), [])

    def test_invalid_values_never_partially_save(self):
        for payload in ({}, {"raw_text": " "}, {"raw_text": 5}, {"raw_text": "test", "parsed": 1},
                        {"raw_text": "test", "energy": True}, {"raw_text": "test", "observations": None},
                        {"raw_text": "test", "occurred_at": "2026-09-29"},
                        {"raw_text": "test", "occurred_at": 100},
                        {"raw_text": "test", "observations": [{"symptom": "test", "source": "ai"}]}):
            with self.subTest(payload=payload):
                result = self.client.post("/api/entries", headers=AUTH, json=payload)
                self.assertEqual(result.status_code, 422)
        self.assertEqual(store.get_entries(self.db), [])
        entry_id = self.create().json()["id"]
        before = store.get_entry(self.db, entry_id)
        result = self.client.patch(f"/api/entries/{entry_id}", headers=AUTH,
                                   json={"raw_text": "changed", "energy": 11})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(store.get_entry(self.db, entry_id), before)

    def test_missing_ids_and_invalid_ranges(self):
        self.assertEqual(self.client.get("/api/entries/999", headers=AUTH).status_code, 404)
        self.assertEqual(self.client.patch("/api/entries/999", headers=AUTH, json={"mood": "calm"}).status_code, 404)
        for url in ("/api/entries/abc", "/api/entries/0", "/api/entries/9223372036854775808", "/api/entries?since=bad",
                    "/api/entries?since=2026-10-01&until=2026-09-01"):
            self.assertEqual(self.client.get(url, headers=AUTH).status_code, 422)

    def test_size_limits(self):
        self.assertEqual(self.create(raw_text="x" * (MAX_BODY_BYTES + 1)).status_code, 413)
        self.assertEqual(self.create(raw_text="x" * 50_001).status_code, 422)
        self.assertEqual(self.create(observations=[{"symptom": "x"}] * 101).status_code, 422)
        result = self.client.post("/api/entries", headers={**AUTH, "Content-Type": "application/json",
                                                         "Content-Length": "invalid"}, content="{}")
        self.assertEqual(result.status_code, 400)

    def test_streaming_limit_without_content_length(self):
        async def run():
            chunks = iter([b"x" * 40_000, b"x" * 40_000])
            messages = []
            async def receive():
                return {"type": "http.request", "body": next(chunks), "more_body": True}
            async def send(message):
                messages.append(message)
            await self.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                            "method": "POST", "path": "/api/entries", "scheme": "http", "query_string": b"",
                            "headers": [(b"host", b"127.0.0.1:8000"), (b"authorization", ("Bearer " + TOKEN).encode()),
                                        (b"content-type", b"application/json")]}, receive, send)
            return messages
        self.assertEqual(asyncio.run(run())[0]["status"], 413)
        self.assertEqual(store.get_entries(self.db), [])

    def test_storage_failures_are_sanitized(self):
        marker = "SENSITIVE_SYNTHETIC_TEXT"
        with patch("journal.api.store.add_entry", side_effect=store.StorageError(marker)):
            response = self.create(raw_text=marker)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(marker, response.text)
        self.assertEqual(store.get_entries(self.db), [])
        with patch("journal.api.store.get_entries", side_effect=RuntimeError(marker)):
            response = self.client.get("/api/entries", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(marker, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_schema_mismatch_returns_conflict_without_migration(self):
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA user_version=1")
        connection.close()
        before = self.db.read_bytes()
        response = self.client.get("/api/entries", headers=AUTH)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.read_bytes(), before)

    def test_errors_do_not_echo_input_and_responses_are_not_cached(self):
        marker = "SENSITIVE_SYNTHETIC_TEXT"
        for response in (self.client.get("/api/health"), self.client.get("/api/entries"),
                         self.client.get("/api/entries/" + marker, headers=AUTH),
                         self.create(energy=marker)):
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(marker, response.text)
            self.assertNotIn(TOKEN, response.text)

    def test_session_tokens_are_isolated(self):
        with TestClient(create_app(self.db, "b" * 43), base_url="http://127.0.0.1:8000") as other:
            self.assertEqual(other.get("/api/entries", headers=AUTH).status_code, 401)

    def test_factory_does_not_create_database(self):
        missing = Path(self.temp.name) / "missing.db"
        with TestClient(create_app(missing, TOKEN), base_url="http://127.0.0.1:8000") as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/api/entries", headers=AUTH).status_code, 503)
        self.assertFalse(missing.exists())

    def test_launcher_binds_loopback_and_cleans_up_private_token(self):
        session_files = []
        captured = io.StringIO()
        def fake_server(app, **options):
            self.assertEqual(options["host"], "127.0.0.1")
            self.assertFalse(options["access_log"])
            self.assertFalse(options["proxy_headers"])
            session_file = Path(captured.getvalue().split("Local session file: ")[1].splitlines()[0])
            session_files.append(session_file)
            self.assertEqual(session_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(session_file.parent.stat().st_mode & 0o777, 0o700)
            session = json.loads(session_file.read_text())
            self.assertNotIn(session["token"], captured.getvalue())
            with TestClient(app, base_url=session["url"]) as client:
                self.assertEqual(client.get("/api/entries", headers={"Authorization": "Bearer " + session["token"]}).status_code, 200)
        with redirect_stdout(captured), patch("journal.serve.uvicorn.run", side_effect=fake_server):
            main(["--db", str(self.db)])
        self.assertFalse(session_files[0].exists())

    def test_launcher_writes_an_explicit_owner_only_session_file(self):
        session_file = Path(self.temp.name) / "session.json"

        def fake_server(app, **options):
            self.assertEqual(session_file.stat().st_mode & 0o777, 0o600)
            session = json.loads(session_file.read_text())
            self.assertEqual(session["url"], "http://127.0.0.1:8000")
            self.assertGreaterEqual(len(session["token"]), 32)

        with patch("journal.serve.uvicorn.run", side_effect=fake_server):
            main(["--db", str(self.db), "--session-file", str(session_file)])
        self.assertTrue(session_file.exists())

    def test_launcher_refuses_to_overwrite_a_session_file(self):
        session_file = Path(self.temp.name) / "session.json"
        session_file.write_text("keep this")
        with self.assertRaises(SystemExit):
            main(["--db", str(self.db), "--session-file", str(session_file)])
        self.assertEqual(session_file.read_text(), "keep this")


if __name__ == "__main__":
    unittest.main()
