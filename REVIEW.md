# Health-bee: review and recommended design

Initial review, September 29, 2026. Scope: the local checkout at /Users/suchitrahari/dev/health-bee, including all three tracked project files and recent commit history. Working tree was clean. At that initial review, no application or tests existed. Days 2–3 have since added storage, editing, health observations, and 29 offline tests. This document remains an architecture review, not a security audit.

## Recommendation

Build a Mac-first local browser app. Each person who installs Health-bee has their own database and local model. Sharing the software does not share journal entries. Phone access comes later, as confirmed by the owner.

| Layer | Choice | Reason |
| --- | --- | --- |
| Interface | React + TypeScript, built with Vite | Standard UI tooling; a small reusable component set |
| Styling | Plain CSS, system fonts | Clean design with no remote assets or extra UI framework |
| Backend | Python + FastAPI + Uvicorn | Keeps the planned Python modules; adds input validation and a small HTTP API |
| Database | SQLite through Python's sqlite3 | One local database; no separate database server or ORM |
| AI | Ollama, with a downloaded Qwen3 4B model as the first candidate | Local extraction and optional summaries; evaluate on the actual Mac before committing to the model |
| Tests | Python unittest in evals/; browser smoke tests when UI exists | Storage and AI failure cases can run offline |
| Distribution | Initially a documented local install and launcher | Package a friendlier Mac installer after the personal version works |

Node/npm is needed to build the UI, not to serve the finished app. FastAPI serves the built static UI and API together on 127.0.0.1. Ollama is the second local process. No hosted backend, account service, vector database, agent framework, or Docker is needed for v1.

React, FastAPI, and their dependencies are proposed choices. The existing AGENTS.md requires approval before introducing dependencies or frameworks. This review installs nothing and does not change that policy.

## What is already good

- AGENTS.md explicitly keeps journal text and parsed fields away from hosted models.
- Storage has a single owner: journal/store.py.
- Raw input must survive model errors and invalid output.
- Offline tests and temporary test databases are already part of the plan.
- .gitignore excludes data/, ordinary database files, environment files, and Python caches.

## Findings from the initial review

1. The original TODO put AI on Day 3 and phone capture on Day 5. The revised plan moves both behind reliable capture, editing, and retrieval.
2. The proposed entries schema contains mood, meds, food, and tags but lacks explicit symptoms, sleep, energy, bleeding, and the date an observation refers to. Add optional fields without assuming regular menstrual cycles or requiring cycle tracking.
3. Separate user-confirmed observations from model suggestions. AI output should never silently replace the user's words or corrections.
4. Treat the written journal entry as the durable record. Commit it before calling the model. If saving fails, keep the text in the editor and show an actionable error; do not claim success.
5. Local storage alone does not encrypt data. SQLite through the standard library is plaintext. Document the initial disk-protection boundary and complete backup/restore work before relying on the app with real entries.
6. Expand .gitignore for SQLite WAL/SHM sidecars, backups, exports, node_modules, and build outputs when those exist. Keep all personal data out of the repository and developer prompts.
7. Add explicit completion criteria. A ten-day outline without tests for each milestone is too optimistic for a private app intended for other people.

## Small first product

Three screens: **Today**, **History**, and **Settings**.

Today has the date, a generous writing area, optional symptom/mood/sleep/energy fields and a Menstrual Period section with a Flow selector, and a clear saved state. Use neutral colors, good contrast, readable system fonts, visible labels, and keyboard access. Cycle tracking is optional. No streaks or pressure to fill every field.

History shows dated entries, search, filters, and editing. Later it adds a weekly review with links back to the entries behind each statement. Settings contains local model status, data location, backup/restore, and export/delete controls.

First AI feature: suggest structured fields from an entry. Second: summarize a chosen week. Keep both optional and manually initiated at first. Neither feature diagnoses conditions, recommends medication, or predicts fertility. Summaries describe recorded observations and distinguish missing data from negative findings.

## Data and module design

```text
Browser UI
    | same-origin HTTP, loopback only
Python/FastAPI
    |-- journal/store.py -> local SQLite
    |-- journal/parse.py -> Ollama on 127.0.0.1:11434
    `-- journal/recall.py -> SQL filters and summaries
```

Start with entries containing an ID, occurrence date/time with offset, created/updated timestamps, raw text, optional confirmed fields, and parse state. A child observations table can hold repeated symptoms and severity without adding a column for every symptom. Keep AI suggestions and model/prompt version separately. Use parameterized SQL, transactions, a schema version, and temporary databases in tests. Keep the existing parsed flag compatible until a more expressive status is deliberately introduced.

The prototype may retain data/journal.db as specified in AGENTS.md. Before sharing, put runtime data in a per-user application-support directory outside the checkout and cloud-synced folders; update the project instructions at that milestone. Moving existing data requires explicit approval and a verified backup.

## Cycle Journal — planned expansion

Use **Menstrual Period** in the product. The current internal `bleeding` field stores flow; naming the UI section does not turn those values into confirmed period records. The terminology update is planned for Day 6, and the complete Cycle Journal for Days 21–25. Existing Day 2–3 functionality stays complete.

| Section | Recorded or calculated content |
| --- | --- |
| Menstrual Period | User-confirmed start/end dates, ongoing status, and daily flow |
| Cycle Phase | User-reported labels kept separate from estimates: Menstrual, Follicular, Ovulatory, Luteal, or Unknown |
| Symptoms | Actual journal observations linked by date |
| Predicted Symptoms | Optional estimates from the user's own history, kept separate from experienced symptoms |

Proposed storage extends the journal with period records, daily cycle observations, phase estimates, and symptom predictions. Period records and daily observations are user data. Estimates and predictions include their supporting record revisions, method version, generation time, target date/window, and uncertainty. Corrections and deletions invalidate dependent estimates; backup, restore, export, and deletion cover every new record type. Final table names and schema details are decided on Day 21.

A future migration must preserve existing flow values without assuming each represents a menstrual period. First test on synthetic data; personal database migration requires explicit approval. No schema changes are part of this planning update.

Keep period history and symptom patterns local. Start with explainable calculations rather than asking the language model to invent phases or future symptoms. Define minimum evidence and applicability rules, return Unknown/insufficient history when necessary, and evaluate on future observations without using those observations as prediction inputs. Missing logs are not negative symptom observations. Any numeric confidence needs calibration before display.

The four phase labels are a simplified UI convention: menstruation overlaps the beginning of the follicular phase. Flow can also occur outside a menstrual period. Do not equate a calendar-based phase estimate with confirmed ovulation. These distinctions inform the data model and display wording. Sources: [NICHD](https://www.nichd.nih.gov/health/topics/menstruation/conditioninfo) and [ACOG](https://www.acog.org/womens-health/faqs/abnormal-uterine-bleeding).

## Privacy requirements

- Bind app and Ollama to loopback. Validate Host and Origin, require a local session token for journal APIs, and protect state-changing requests against CSRF. CORS alone is insufficient.
- Set OLLAMA_NO_CLOUD=1 for the actual Ollama process, restart, and verify cloud is disabled. Allow only the selected installed local model; reject remote endpoints and cloud model selections. Do not follow redirects or use proxy environment settings for model requests.
- Bundle fonts, scripts, and styles. No analytics, remote error collection, external assets, automatic model downloads, or remote fallback. Downloads and upgrades are explicit setup/maintenance steps. Review Ollama's own automatic-update behavior when validating a strict no-outbound configuration.
- Keep request bodies, model prompts, outputs, and search terms out of logs. Return Cache-Control: no-store for sensitive responses; keep journal text out of URL query strings and persistent browser storage.
- Use synthetic entries in development, tests, screenshots, bug reports, and coding assistants. Real health records should never enter a hosted developer tool.
- For the personal prototype, verify FileVault and use an encrypted local backup destination. FileVault protects a locked disk; it does not stop software running in an unlocked account from reading files. Browser extensions and a compromised Mac remain outside this app's protection.
- Before broad sharing, choose and test app-level encryption and key recovery (for example SQLCipher with a maintained Python binding), or explicitly limit the release to a documented OS-encryption model. Do not claim the default SQLite file is encrypted. Encryption adds dependencies and packaging work, so it needs a deliberate milestone.
- Export only on explicit request. Explain that exported files and old backups may retain entries after deletion. Avoid promising secure erasure on SSDs.

## AI evaluation

Qwen3 4B is a starting candidate, not a medical model or a proven fit for this Mac. Measure memory, latency, and extraction accuracy with short synthetic entries and a bounded context window. Download size is not runtime memory usage. Choose a smaller model if needed; journaling stays available without any model.

Use Ollama structured output with a JSON schema, then independently validate types, allowed values, lengths, and dates. Treat journal text as data, including text that looks like instructions. Give the model no tools or database-writing access. Timeouts, malformed output, and service outages leave the saved entry intact. User edits invalidate stale suggestions.

## Sources checked

- React with TypeScript: https://react.dev/learn/typescript
- Vite templates: https://vite.dev/guide/
- FastAPI and Uvicorn: https://fastapi.tiangolo.com/deployment/manually/
- SQLite design: https://www.sqlite.org/about.html
- Ollama local-only mode and network defaults: https://docs.ollama.com/faq
- Ollama structured output: https://docs.ollama.com/capabilities/structured-outputs
- Initial model candidate: https://ollama.com/library/qwen3:4b
- FileVault protection: https://support.apple.com/guide/mac-help/protect-data-on-your-mac-with-filevault-mh11785/mac
