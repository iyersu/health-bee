# Journal — project instructions

## What this is
A local-first daily journal. Free text in, structured rows in one SQLite
file at data/journal.db. Nothing about my entries leaves this machine.

## Hard rules
- Never write to data/ except through the storage module (journal/store.py).
- Never delete, migrate, or reset data/journal.db without asking me first,
  every single run. Permission does not carry over between turns.
- No new runtime dependency without asking. Python stdlib + sqlite3 by default.
- The parse step must degrade gracefully: if the model fails or returns bad
  JSON, store the raw text with parsed=0. Never drop input.
- Journal content (raw text, parsed fields) must only be sent to the local
  Ollama endpoint. Never to a hosted model, ever.

## Working style
- One change at a time. Show me the plan and the diff before writing files.
- Every behavior change gets a test in evals/ that runs offline (no network).
- Commit when something works. Subject = what, body = why.
- Prefer boring code. If you reach for a framework, stop and ask.

## Layout
- journal/       app code (capture, parse, store, recall)
- evals/         offline tests, especially parse golden files
- data/          runtime data (gitignored, never touch directly)
- AGENTS.md      this file
- TODO.md        what's next

