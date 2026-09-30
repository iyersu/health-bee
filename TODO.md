# Health-bee — daily implementation plan

Mac first; phone access later. All journal storage and AI processing stay on the user's machine.

## How to use this file

Each day is one focused work session, roughly 1–3 hours where practical, not a deadline. Split larger sessions as needed. Start the next day only when the current completion check passes. Packaging and encryption may need several sessions. This file is a plan, not an automation that runs each day.

For each session: select the unchecked day, show the planned change and diff as required by AGENTS.md, implement the smallest complete slice, run relevant offline checks, and record evidence below. Commit working changes as required by AGENTS.md. Use synthetic entries and temporary databases for tests. Ask before adding runtime dependencies/frameworks or changing an existing personal database. Never include personal journal text in a hosted coding assistant.

## Cycle Journal scope

Use **Menstrual Period** as the user-facing section name. The current schema-v2 `bleeding` field records flow only; it does not establish period status, start/end dates, or a cycle phase. Keep this implementation detail out of UI labels. Day 6 introduces the wording; Days 21–25 deliver the full optional Cycle Journal after the initial journal pilot.

The Cycle Journal has four sections: Menstrual Period, Cycle Phase, Symptoms, and Predicted Symptoms. Phase labels are Menstrual, Follicular, Ovulatory, and Luteal, with Unknown available. Recorded facts, user-reported phase labels, computed estimates, and predicted symptoms must remain distinguishable. Calculations and all supporting history stay local. Completion of Days 2–3 does not imply these later features exist.

## Milestone 1 — reliable journaling

### Day 1 — project review and decisions

- [x] Review current repository and privacy rules.
- [x] Confirm Mac browser first, phone later.
- [x] Draft stack recommendation and this daily plan.
- [ ] Confirm React/TypeScript/Vite, Python/FastAPI/Uvicorn, SQLite, and Ollama before installing their dependencies.
- [ ] Record Mac chip, available memory, and supported tool versions; validate the model choice later with measurements.
- Done when: scope and initial dependency choices are recorded; development uses synthetic data.

### Day 2 — storage foundation

- [x] Implement journal/store.py with init_db, add_entry, get_entry, and date-range listing.
- [x] Add timestamps, occurrence date, raw text, optional confirmed fields, and schema version; retain parsed=0 for unprocessed text.
- [x] Test insert/read/reopen, empty-input handling, Unicode, and date boundaries in evals/test_store.py using a temporary database.
- Done when: an entry survives process restart and tests never touch personal data.

### Day 3 — editing and structured observations

- [x] Add optional symptoms, mood, sleep, energy, bleeding, medications, food, and tags; support missing values.
- [x] Support editing and repeated observations; keep AI suggestions separate from confirmed values.
- [x] Test transactions, validation, and user corrections. Use only fresh test databases while designing the schema.
- Done when: incomplete entries and corrections round-trip without losing the raw journal text.

### Day 4 — local API and access boundary

- [x] Add the approved FastAPI dependencies and entry create/read/update endpoints.
- [x] Bind to 127.0.0.1; add Host/Origin checks, a local session token, and CSRF protection.
- [x] Suppress sensitive logs; use no-store responses and validated request limits.
- [x] Test rejected unauthorized/cross-origin requests and persistence failures offline.
- Done when: only the intended local UI session can access entries.

### Day 5 — clean interface shell

- [ ] Scaffold the approved React/TypeScript/Vite frontend with plain CSS and system fonts.
- [ ] Build Today, History, and Settings navigation with synthetic content; reserve Cycle Journal navigation for Day 22 when it has usable features.
- [ ] Check keyboard navigation, contrast, focus, and a narrow browser window.
- Done when: the three screens are readable and usable with no remote fonts or scripts.

### Day 6 — capture and save

- [ ] Connect the Today editor and optional observation fields to the API.
- [ ] Use Menstrual Period as the section label and Flow for the existing flow selector. Explain that flow alone does not confirm a period; preserve missing values and allow spotting to be recorded without classifying it as a period.
- [ ] Show saving/saved/error states and preserve editor text when saving fails.
- [ ] Prevent accidental double submission; warn before leaving with unsaved edits.
- Done when: an entry can be saved and reopened after restart with the network disconnected.

### Day 7 — history, search, and edit

- [ ] Add date filters, simple parameterized SQL search, and editing from History.
- [ ] Keep search text out of URLs and access logs; handle local dates consistently.
- [ ] Test multiple entries per day, no results, and edits.
- Done when: a user can find and correct a past entry without AI.

### Day 8 — local backup and restore

- [ ] Add consistent SQLite backups through the storage module, targeting an encrypted local destination.
- [ ] Restore into a temporary database first and verify records before any replacement.
- [ ] Explain recovery and verify FileVault before using real personal entries.
- Done when: a synthetic journal survives backup, simulated loss, and restore. Replacing a real database still requires explicit approval.

## Milestone 2 — optional local AI

### Day 9 — local model setup

- [ ] Install Ollama and explicitly download a candidate local model after dependency approval.
- [ ] Disable cloud features for the running process; restrict endpoint/model selection and redirects.
- [ ] Benchmark Qwen3 4B on synthetic entries; record memory, latency, model digest, and context limit.
- Done when: a disconnected local inference succeeds and an unavailable model produces a useful status without blocking journaling.

### Day 10 — extraction contract and fixtures

- [ ] Define a JSON schema for optional suggestions with source evidence.
- [ ] Create at least 15 synthetic input/expected fixtures: absent information, negation, ambiguous dates, multiple symptoms, and instruction-like journal text. Include spotting versus an explicitly reported period; do not infer a confirmed period or phase from flow alone.
- [ ] Build offline mocked-response tests for schema validation and timeouts.
- Done when: invalid or invented fields are rejected and tests make no model/network calls.

### Day 11 — durable save before parsing

- [ ] Implement journal/parse.py; commit the original entry before any model request.
- [ ] Keep parsing manually initiated, bounded, and retryable; record model/prompt version and parse status.
- [ ] Test unavailable service, malformed JSON, timeout, and stale results after editing.
- Done when: every AI failure preserves the saved entry and does not overwrite confirmed observations.

### Day 12 — review AI suggestions

- [ ] Add an Analyze button and editable suggestion review with accept/reject actions.
- [ ] Show the journal excerpt supporting each suggestion.
- [ ] Keep optional fields blank when unknown; test that rejecting suggestions preserves the entry.
- Done when: users control which suggestions become confirmed observations.

### Day 13 — weekly review

- [ ] Build SQL-based counts and a date-range review first.
- [ ] Add an optional bounded local summary with links to supporting entries; disclose truncation or missing days.
- [ ] Test that summaries avoid unsupported diagnoses, causal claims, treatment advice, and fertility predictions.
- Done when: each factual summary statement can be checked against saved entries and the review works without AI.

### Day 14 — AI evaluation gate

- [ ] Run the fixtures against the installed local model separately from offline CI tests.
- [ ] Record field accuracy, unsupported suggestions, invalid-output rate, and latency; inspect every failure.
- [ ] Block AI release for lost inputs or overwritten corrections; keep low-quality extraction disabled until improved.
- Done when: results and limitations are documented and useful behavior has been demonstrated on this Mac.

## Milestone 3 — personal use and sharing

### Day 15 — privacy verification

- [ ] Inspect browser requests and process network activity; test ordinary use with external network access blocked.
- [ ] Check model/cloud restrictions, bundled assets, update behavior, logs, browser persistence, and unauthorized API requests.
- [ ] Expand ignore patterns for WAL/SHM files, backups, exports, dependencies, and builds.
- Done when: capture, history, and installed-model inference work offline, with no journal content in logs or external requests.

### Day 16 — export and deletion

- [ ] Add explicit local JSON/CSV export and entry deletion with confirmation.
- [ ] Explain export sensitivity and that old backups retain old entries; test against synthetic data.
- [ ] Ensure deletion invalidates related suggestions and cached summaries.
- Done when: exported data round-trips and deletion has clearly documented limits.

### Day 17 — everyday Mac launch

- [ ] Build UI assets; serve them from the backend so daily use needs no Vite server.
- [ ] Add a launcher, useful startup errors, clean shutdown, and an optional launchd setup.
- [ ] Before distribution, configure a per-user application-support data directory and update AGENTS.md accordingly; never move an existing database without approval and backup.
- Done when: the app opens from one launcher and restart preserves records.

### Day 18 — use it personally and fix friction

- [ ] Use the app for several actual days; record usability issues without copying private entries into development tools.
- [ ] Fix the three most frequent problems; revisit accessibility and save/error clarity.
- [ ] Re-test recovery and model-off behavior.
- Done when: daily writing feels reliable. This checkpoint may span a week.

### Day 19 — protection and packaging for others

- [ ] Choose a documented encryption boundary for the first shared release; evaluate app encryption/key recovery if stronger protection is required.
- [ ] Test the chosen protection and backup flow. SQLCipher or other new dependencies require approval and separate implementation sessions.
- [ ] Write installation, model download, data location, recovery, and uninstall instructions; confirm dependency/model licensing before redistributing.
- [ ] Test a clean macOS account with no developer environment assumptions.
- Done when: another person can install and recover their own local journal, and privacy claims match verified behavior.

### Day 20 — small pilot release

- [ ] Prepare a tagged release with synthetic screenshots and known limitations.
- [ ] Let one or two people install independently; collect feedback without health records or automatic telemetry.
- [ ] Fix installation/data-loss issues before widening access.
- Done when: the pilot runs with separate local databases and models. Publishing or contacting testers is a separate explicit action.

## Milestone 4 — Cycle Journal and personal insights

These are later work sessions, not a commitment to enable predictions after 25 calendar days. Collecting enough useful history and evaluating estimates may take several cycles. Core journaling remains available when cycle tracking or estimates are disabled.

### Day 21 — Menstrual Period records and schema design

- [ ] Define period records with user-confirmed start date, optional end date, and notes; support ongoing and uncertain records.
- [ ] Add daily cycle observations with local date, optional user-reported period status, flow, and links to journal entries. Keep spotting outside a confirmed period representable.
- [ ] Decide how multiple journal entries on one date link to one daily cycle record; handle conflicts explicitly rather than silently overwriting values.
- [ ] Specify a versioned migration from schema v2. Preserve existing flow without assuming that it indicates menstruation. Verify migration and recovery on synthetic copies; seek explicit approval before any personal-database migration.
- [ ] Test date ranges, optional values, conflicting/overlapping period records, and correction behavior.
- Done when: period events and daily flow are stored separately, and existing recorded facts survive the tested migration path.

### Day 22 — Cycle Journal UI

- [ ] Add Cycle Journal navigation with Menstrual Period and recorded Symptoms sections and a calendar/timeline.
- [ ] Let users record and correct period dates and flow; link symptoms to their actual journal dates.
- [ ] Display missing days as unrecorded. Keep cycle tracking optional and avoid forcing regular-cycle assumptions.
- [ ] Include period/cycle records in local backup, restore, export, and deletion; test the entire round-trip.
- Done when: a user can review and correct recorded cycle history offline without enabling estimates or AI.

### Day 23 — estimated cycle phase

- [ ] Define and document a local calculation method, its required inputs, applicability limits, and an explicit insufficient-data rule before implementation.
- [ ] Support the display labels Menstrual, Follicular, Ovulatory, Luteal, and Unknown. Treat these as a simplified display model; preserve user-reported labels separately from calculated estimates.
- [ ] Store estimated date/window, method version, generation time, supporting record revisions, and uncertainty. Date history alone must not be represented as confirmed ovulation.
- [ ] Handle irregular/missing history and contexts where phase estimation does not apply by returning Unknown instead of forcing a fixed 28-day cycle.
- [ ] Invalidate/recompute estimates after supporting period dates or observations change or are deleted.
- Done when: every displayed estimate explains its basis and limitations, with tested Unknown behavior. Local AI may explain calculated results but must not invent phases.

### Day 24 — predicted symptoms from personal history

- [ ] Start with simple local counts/patterns from the user's recorded symptoms; define observation coverage and a minimum-data rule before showing predictions.
- [ ] Keep predictions in separate records with symptom, target date/window, supporting records, method version, and uncertainty. Do not insert predictions into recorded observations.
- [ ] Do not treat an unlogged symptom as absent; permit explicit absence when measuring accuracy. Account for uncertain phase estimates if used as an input.
- [ ] Show insufficient history when evidence is sparse. Avoid numeric confidence unless it has been evaluated and calibrated.
- [ ] Invalidate affected predictions after source corrections/deletion, and let users disable predictions independently of journaling.
- Done when: a prediction is traceable to personal history and never appears as something the user actually experienced.

### Day 25 — evaluate and release cycle insights

- [ ] Test synthetic regular, irregular, missing, and corrected histories; verify phase uncertainty and invalidation.
- [ ] Evaluate symptom predictions on later observations using only earlier history as inputs; document accuracy, missing-data handling, and limitations against a simple baseline.
- [ ] Enable predictions only after their documented evaluation criteria pass; keep them off if evidence is insufficient.
- [ ] Recheck offline operation, local data protection, backup/export/deletion coverage, and accessibility for every new cycle record type.
- [ ] Explain simplified phase labels and clearly distinguish recorded symptoms from estimates and predictions. Do not present the feature as confirmation of ovulation or contraception guidance.
- Done when: users can understand and correct recorded data, inspect estimates, and disable insights; the release documents what has actually been validated.

## Later backlog

- Phone capture after deciding whether the phone stores/processes data itself or connects to the Mac. A responsive UI alone does not make the app available offline on a phone.
- Secure device access/pairing; reconsider the previous Shortcut + Tailscale idea against the strict local-only requirement before adopting it.
- Further user-requested trends after the Cycle Journal milestone, without causal or diagnostic claims.
- Mac app packaging, additional operating systems, and opt-in manual update checks.
- App-level encryption if deferred, with tested key recovery and migration.

## Session record

| Session/date | Completed | Evidence/checks | Next smallest step |
| --- | --- | --- | --- |
| Planning — 2026-09-29 | Repository reviewed; Mac-first scope confirmed; stack and daily plan drafted | Three tracked files inspected; working tree initially clean; official technical docs checked | Confirm proposed dependencies, then Day 2 storage |
| Day 2 — 2026-09-29 | SQLite storage, schema validation, and offline tests implemented | Python 3.9.6; 14 tests passed with `python3 -m unittest discover -s evals -v`; includes a new-process persistence check | Day 3: editing and structured observations |
| Day 3 — 2026-09-29 | Partial edits, optional health fields, repeated symptoms, user provenance, and entry revisions | 29 offline tests passed with `python3 -m unittest discover -s evals -v`; schema v2 uses fresh databases, no automatic v1 migration | Day 4: local API and access boundary |
| Cycle Journal planning — 2026-09-29 | Added Menstrual Period wording and Days 21–25 for cycle records, phase estimates, personal symptom predictions, and evaluation | Documentation review only; schema v2 and completed Day 2–3 work preserved | Continue Day 4; introduce UI wording on Day 6 |
| Day 4 — 2026-09-30 | Local authenticated loopback API, launcher, pinned dependencies, and startup documentation | 46 offline tests passed with Python 3.12.14; live socket smoke test blocked by sandbox network policy; API binds loopback in launcher | Day 5: clean interface shell |
