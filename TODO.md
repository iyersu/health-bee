## Day 2 — storage, no AI
- journal/store.py: init_db(path), add_entry(...), get_entries(since, until)
- Schema: entries(id INTEGER PK, ts TEXT, raw_text TEXT, mood TEXT,
  meds TEXT, food TEXT, tags TEXT, parsed INTEGER DEFAULT 0)
- evals/test_store.py: create → insert → read → assert. Uses a temp db,
  not data/journal.db.

## Day 3 — parse prompt
- journal/parse.py: raw_text → dict via local model
- evals/golden/*.json: 15 hand-written input/expected pairs
- Test runs offline with a mocked model, plus a live run against ollama

## Day 4 — CLI capture
## Day 5 — phone capture (iOS Shortcut + Tailscale)
## Day 6 — recall (SQL only)
## Day 7 — weekly summary
## Day 8 — eval gate
## Day 9 — launchd + backups
## Day 10 — dogfood review
