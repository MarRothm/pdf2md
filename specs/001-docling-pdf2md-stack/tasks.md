---
description: "Task list for feature implementation"
---

# Tasks: Offline Docling PDF-to-Markdown Stack

**Input**: Design documents from `/specs/001-docling-pdf2md-stack/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: The spec does not request tests explicitly, but [plan.md](./plan.md) fixes a test stack (`pytest`, `pytest-asyncio`, `httpx` ASGI transport, a stub engine) and a `tests/` layout. Test tasks are therefore included where they encode a contract or a spec acceptance scenario. They are not exhaustive TDD-per-task — drop the test tasks if you want implementation only.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested, and demonstrated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths are given in every task

## Path Conventions

Single Python project at the repository root, per [plan.md](./plan.md): `src/pdf2md/`, `tests/`, `deploy/`, `ops/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton, dependencies, and tooling

- [X] T001 Create the source tree from plan.md — `src/pdf2md/`, `src/pdf2md/api/`, `src/pdf2md/static/`, `tests/{unit,contract,integration}/`, `deploy/`, `ops/` — each with a `.gitkeep` or `__init__.py` as appropriate
- [X] T002 Create `pyproject.toml` declaring Python 3.12, runtime deps (`fastapi`, `uvicorn[standard]`, `httpx`, `python-multipart`, `pydantic`, `pydantic-settings`) and a `dev` extra (`pytest`, `pytest-asyncio`, `ruff`)
- [X] T003 [P] Configure `ruff` lint and format rules in `pyproject.toml` and add `.gitignore` covering `.venv/`, `__pycache__/`, `*.sqlite*`, `deploy/.env`
- [X] T004 [P] Write `Dockerfile` for the web image — `python:3.12-slim`, `linux/arm64`, non-root user, no build toolchain in the final layer, `CMD` running uvicorn on port 8080
- [X] T005 [P] Configure pytest in `pyproject.toml` — `asyncio_mode=auto`, testpaths, and markers `contract`, `integration`, `unit`
- [X] T006 [P] Create `.dockerignore` excluding `tests/`, `specs/`, `.venv/`, and `deploy/.env` from the build context

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Configuration, persistence, and the app shell that every user story builds on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T007 Implement settings in `src/pdf2md/config.py` — every `PDF2MD_*` variable from [contracts/stack.md](./contracts/stack.md) with its documented default, loaded via `pydantic-settings`, failing fast on a missing `PDF2MD_ENGINE_API_KEY`
- [X] T008 [P] Define Pydantic models in `src/pdf2md/models.py` for `Batch`, `SourceDocument`, `ConversionJob`, `MarkdownOutput`, the `JobStatus` enum, and the API payload shapes from [contracts/web-api.md](./contracts/web-api.md)
- [X] T009 Implement `src/pdf2md/db.py` — the full DDL from [data-model.md](./data-model.md), a migration runner, and a connection helper setting `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` on every connection
- [X] T010 [P] Implement `src/pdf2md/storage.py` — inbox/outbox path resolution, an atomic write helper (temp file → `fsync` → rename), free-space checks, and a writability probe used by health
- [X] T011 [P] Implement `src/pdf2md/naming.py` — `output_filename(original_name, content_hash) -> "{slug}--{hash12}.md"` per [research.md](./research.md) R8, with filename sanitization and slug collision-free behavior
- [X] T012 [P] Configure structured logging in `src/pdf2md/logging_config.py` — every log line for a job carries job id, source filename, and outcome so Portainer logs satisfy FR-019
- [X] T013 Implement the app factory in `src/pdf2md/main.py` — FastAPI instance, lifespan running migrations and starting the dispatcher, static mount at `/`, and `GET /healthz` per [contracts/web-api.md](./contracts/web-api.md)
- [X] T014 [P] Create the page shell `src/pdf2md/static/index.html` and `src/pdf2md/static/styles.css` — self-hosted assets only, no CDN link, no external font, no analytics (FR-025)
- [X] T015 [P] Build a stub `docling-serve` in `tests/stubs/docling_stub.py` implementing the async task API from [contracts/docling-serve.md](./contracts/docling-serve.md), including single-use result semantics and injectable failure/delay modes
- [X] T016 Create `tests/conftest.py` — temp inbox/outbox/db fixtures, an ASGI client fixture, and a fixture wiring the app to the stub engine from T015
- [X] T017 [P] Unit tests for naming in `tests/unit/test_naming.py` — same bytes give the same name, different bytes under one filename give different names, hostile filenames are sanitized
- [X] T018 [P] Unit tests for schema and settings in `tests/unit/test_db.py` and `tests/unit/test_config.py` — migrations are idempotent, the status CHECK constraint rejects unknown states, defaults match contracts/stack.md

**Checkpoint**: App starts, serves `/healthz`, and has a durable schema — user stories can now begin

---

## Phase 3: User Story 1 - Convert a complex PDF into ingestion-ready Markdown (Priority: P1) 🎯 MVP

**Goal**: A LAN user uploads a complex PDF through the browser page, watches it convert, and retrieves faithful Markdown — multi-column reading order, tables, and text recovered from scanned pages.

**Independent Test**: Upload a PDF containing a multi-column page, a table, and a scanned page; confirm the Markdown has correct reading order, a Markdown table, and recognized text from the scan. Runs against the stub engine for logic, and against the real engine for fidelity.

### Tests for User Story 1

- [X] T019 [P] [US1] Contract test for `POST /api/uploads` in `tests/contract/test_uploads.py` — 202 shape, `accepted`/`rejected` split, rejection reasons for non-PDF, zero-byte, and oversized files
- [X] T020 [P] [US1] Contract test for `GET /api/jobs`, `GET /api/jobs/{id}`, and the Markdown download in `tests/contract/test_jobs.py` — field presence, `display_status` mapping, `download_url` only on success, `Content-Disposition` filename
- [X] T021 [P] [US1] Contract test for the engine client in `tests/contract/test_docling_client.py` — request shape sent to `/v1/convert/file/async`, `X-Api-Key` header, and parsing of poll and result payloads
- [X] T022 [P] [US1] Integration test in `tests/integration/test_convert_flow.py` — upload → queued → running → succeeded → Markdown present in the outbox and downloadable, driven by the stub engine
- [X] T023 [P] [US1] Integration test in `tests/integration/test_bad_input.py` — corrupt and password-protected PDFs end as `failed` with a human-readable reason and **no** file in the outbox (FR-007, spec US1 scenario 4)

### Implementation for User Story 1

- [X] T024 [US1] Implement the engine client in `src/pdf2md/docling_client.py` — async submit/poll/result against the endpoints in [contracts/docling-serve.md](./contracts/docling-serve.md), sending `from_formats=pdf`, `to_formats=md`, `do_ocr=true` explicitly, with the API key header
- [X] T025 [US1] Implement engine-to-job status mapping in `src/pdf2md/docling_client.py` — the mapping table in contracts/docling-serve.md, translating engine `errors[]` into non-technical `failure_reason` text while preserving raw detail in `engine_errors`
- [X] T026 [US1] Implement the dispatcher loop in `src/pdf2md/dispatcher.py` — claim `queued` jobs, submit, poll at `POLL_INTERVAL_SECONDS`, and drive state transitions per the [data-model.md](./data-model.md) state machine
- [X] T027 [US1] Implement fetch-and-persist as one indivisible step in `src/pdf2md/dispatcher.py` — call `/v1/result/{task_id}` exactly once, write Markdown atomically to the outbox, then commit `MarkdownOutput` and `succeeded` in a single transaction; any failure after the fetch marks the job `failed` with a reason naming the lost result (research.md R3)
- [X] T028 [US1] Implement PDF validation in `src/pdf2md/api/uploads.py` — magic-byte check, size limit, zero-byte rejection, SHA-256 hashing while streaming to the inbox so a document is never held in memory in full
- [X] T029 [US1] Implement `POST /api/uploads` in `src/pdf2md/api/uploads.py` returning the 202 payload from contracts/web-api.md, creating `SourceDocument` and `ConversionJob` rows
- [X] T030 [US1] Implement `GET /api/jobs` and `GET /api/jobs/{id}` in `src/pdf2md/api/jobs.py`, including `display_status` derivation so the page never maps states itself
- [X] T031 [US1] Implement `GET /api/jobs/{id}/markdown` in `src/pdf2md/api/jobs.py` — `text/markdown`, `Content-Disposition` filename identical to the outbox filename, 409 while still converting, 404 when absent (FR-012)
- [X] T032 [US1] Implement the page behavior in `src/pdf2md/static/app.js` — file picker, upload, a status list polling `GET /api/jobs`, per-job failure reason, and a download link on success; vanilla ES2022, no framework, no external request
- [X] T033 [US1] Add per-job logging in `src/pdf2md/dispatcher.py` — submission, each state change, and outcome, so a failure is diagnosable from Portainer logs alone (FR-019)
- [X] T092 [US1] Implement suspect-yield detection in `src/pdf2md/dispatcher.py` — compute characters-per-page against `SUSPECT_MIN_CHARS_PER_PAGE` during the persist step (T027) and set `succeeded_suspect` instead of `succeeded`; the Markdown is written either way (FR-029)
- [X] T093 [P] [US1] Integration test in `tests/integration/test_suspect_yield.py` — a blank-scan PDF and a near-empty result both report `succeeded_suspect` with the output still downloadable, and a normal document does not trip the threshold (FR-029)
- [X] T094 [US1] Render `succeeded_suspect` distinctly in `src/pdf2md/static/app.js` — visually separated from plain success, with the download still offered (FR-029)

**Checkpoint**: A single complex PDF converts end to end and is retrievable — this is the MVP

---

## Phase 4: User Story 2 - Operate the stack from Portainer on the Mac mini (Priority: P1)

**Goal**: An operator deploys, redeploys, and inspects the whole stack from Portainer, and it survives a Mac mini reboot unattended.

**Independent Test**: Deploy the stack in Portainer from the provided definition with no host-side steps beyond creating the outbox directory; confirm both services report healthy, redeploy and confirm outputs and history persist, reboot the Mac mini and confirm unattended recovery.

### Tests for User Story 2

- [X] T034 [P] [US2] Contract test for `GET /api/health` in `tests/contract/test_health.py` — the payload from contracts/web-api.md, 503 with `"degraded"` when the engine is unreachable, and uploads still accepted while degraded
- [X] T035 [P] [US2] Integration test in `tests/integration/test_persistence.py` — job history and outbox contents survive a simulated restart of the app against the same volumes (FR-017)

### Implementation for User Story 2

- [X] T036 [US2] Write `deploy/docker-compose.yml` — the `web` and `docling` services from [contracts/stack.md](./contracts/stack.md) with pinned image tags, `pull_policy: never`, and `restart: unless-stopped` (FR-016). Networks are added by US3. *(Image references and pull policy revised by T101–T102)*
- [X] T037 [US2] Declare volumes in `deploy/docker-compose.yml` — named `db` and `inbox` volumes, and the `${OUTBOX_HOST_PATH}` bind mount; the SQLite database must not be on a bind mount (research.md R7)
- [X] T038 [US2] Set every `docling` environment variable from contracts/stack.md in `deploy/docker-compose.yml` — worker count, `SHARE_MODELS`, timeouts, file and page limits, `DOCLING_DEVICE=cpu`, queue size (FR-027, FR-028)
- [X] T039 [US2] Add healthchecks and `depends_on: docling: condition: service_healthy` in `deploy/docker-compose.yml` so Portainer reports true health and the page never starts against a cold engine (FR-018). Confirm the engine's health path against `/docs` first (research.md O1)
- [X] T040 [US2] Implement `GET /api/health` in `src/pdf2md/api/health.py` — engine reachability, backlog counts, outbox writability and free space, database writability
- [X] ~~T041~~ [P] [US2] **SUPERSEDED 2026-08-19 by T105/T106** — the air-gap transfer path no longer exists (research.md R5). Was: write `ops/save-images.sh` — pull the pinned engine tag for `linux/arm64`, **verify `/opt/app-root/src/.cache/docling/models` is populated and abort if not**, build the web image, `docker save | gzip` both, print SHA-256 checksums (research.md R4, R5)
- [X] ~~T042~~ [P] [US2] **SUPERSEDED 2026-08-19 by T105** — was: write `ops/load-images.sh` — verify checksums, `docker load` both archives, assert both pinned tags are present locally
- [X] T043 [P] [US2] Write `deploy/.env.example` with every tunable and its chosen default, and no secret values
- [X] T044 [P] [US2] Write `deploy/README.md` — Portainer deploy and redeploy steps, the "re-pull image toggle stays OFF" warning, and the storage locations with their purpose and expected growth (FR-020). *(Deployment sections rewritten by T107)*
- [X] T045 [US2] Set `mem_limit` on both services in `deploy/docker-compose.yml` from measured engine RSS at 2 workers with `SHARE_MODELS=true`, leaving headroom for Portainer on the 8.38 GB VM (SC-011, research.md R6)

**Checkpoint**: The stack deploys and redeploys from Portainer alone and survives a reboot

---

## Phase 5: User Story 3 - Guarantee the stack is offline and LAN-only (Priority: P1)

**Goal**: Isolation is a structural property of the stack definition, and the operator can prove it on demand.

**Independent Test**: With the host's internet path blocked, deploy from scratch and convert a document end to end; separately confirm the page answers on the LAN and is unreachable from outside it.

**⚠️ Depends on T036** — US3 edits the compose file US2 creates. If US3 is done first, create `deploy/docker-compose.yml` here instead.

### Tests for User Story 3

- [X] T046 [P] [US3] Test in `tests/unit/test_static_assets.py` that no file under `src/pdf2md/static/` references an external origin — scan for `http://`, `https://`, and `//` URLs in `index.html`, `app.js`, and `styles.css` (FR-025)
- [X] T047 [P] [US3] Test in `tests/unit/test_no_egress_paths.py` that the codebase makes no outbound call other than to `PDF2MD_ENGINE_URL` — assert every `httpx` base URL originates from settings

### Implementation for User Story 3

- [X] T048 [US3] Add the two networks to `deploy/docker-compose.yml` — `core` with `internal: true`, and `edge` as a bridge with `com.docker.network.bridge.enable_ip_masquerade: "false"` (research.md R1)
- [X] T049 [US3] Attach services in `deploy/docker-compose.yml` — `web` on both `edge` and `core` with only its port published; `docling` on `core` alone with no ports (FR-023)
- [X] T050 [US3] Set the isolation environment variables on `docling` in `deploy/docker-compose.yml` — `ARTIFACTS_PATH`, `LOAD_MODELS_AT_BOOT=true`, `ENABLE_REMOTE_SERVICES=false`, `ALLOW_EXTERNAL_PLUGINS=false`, `ENABLE_UI=false` (FR-021, FR-022)
- [X] T051 [US3] Wire `DOCLING_SERVE_API_KEY` from the Portainer stack variable in `deploy/docker-compose.yml` and send it from the client, keeping the web service itself credential-free for users (FR-024, research.md R9)
- [X] T052 [P] [US3] Write `ops/verify-offline.sh` — assert engine egress fails, web egress fails, and `web → docling:5001` succeeds; then run a real conversion and assert no download errors in the engine log (FR-026)
- [X] T053 [P] [US3] Write `ops/verify-lan-only.sh` — assert HTTP 200 from the Mac mini's LAN address, assert the `docling` service publishes no ports, and prompt the operator to confirm no router port-forward maps the port (FR-026)
- [X] T054 [P] [US3] Document the isolation model and both verification procedures in `deploy/README.md` — including the warning that consolidating onto one `internal` network breaks published ports (research.md R1)
- [X] T055 [US3] Add a startup assertion in `src/pdf2md/main.py` that `PDF2MD_ENGINE_URL` resolves to a private address, refusing to start against a public host

**Checkpoint**: Isolation is enforced by the stack file and provable with two scripts

---

## Phase 6: User Story 4 - Collect converted Markdown for import into AnythingLLM (Priority: P2)

**Goal**: Every successful conversion lands in one outbox folder under a stable, unique, traceable name, ready for the operator's manual AnythingLLM import.

**Independent Test**: Convert a small set of PDFs, confirm each appears in the outbox with a `{slug}--{hash12}.md` name and downloads from the page under the identical name, then import the folder into AnythingLLM and confirm answers cite the right documents.

### Tests for User Story 4

- [X] T056 [P] [US4] Integration test in `tests/integration/test_dedup.py` — converting identical bytes twice yields one outbox file, the second job reports `already_converted`, and a renamed copy of the same bytes resolves to the same output (FR-014, spec US4 scenario 3)
- [X] T057 [P] [US4] Integration test in `tests/integration/test_outbox.py` — no file appears for a failed job; an interrupted write leaves no truncated `.md`; download filename equals the outbox filename

### Implementation for User Story 4

- [X] T058 [US4] Implement the `already_converted` path in `src/pdf2md/dispatcher.py` — on claiming a `queued` job, if a `MarkdownOutput` exists for the content hash **and** the file is present in the outbox, terminate the job as `already_converted` with the existing `output_filename` and do no engine work
- [X] T059 [US4] Persist `MarkdownOutput` rows in `src/pdf2md/db.py` with `engine_status` recording `success` versus `partial_success`, and surface `partial_success` distinctly rather than as plain success (research.md O4)
- [X] T060 [US4] Implement `POST /api/jobs/{id}/retry` in `src/pdf2md/api/jobs.py` — creates a new job for the same source, 409 when the original succeeded or the inbox PDF was already reaped
- [X] T061 [US4] Implement the outbox inventory query in `src/pdf2md/db.py` and expose it in `GET /api/health` as a document count, giving the operator an import checklist (data-model.md derived views)
- [X] T062 [US4] Show `already_converted` and `partial_success` distinctly in `src/pdf2md/static/app.js`, including the existing output filename so the user knows where the document already is
- [X] T063 [US4] Implement history pruning in `src/pdf2md/db.py` — prune jobs older than `JOB_HISTORY_DAYS` while **never** deleting `markdown_output` rows or outbox files (data-model.md)
- [X] T064 [US4] Document the manual AnythingLLM import step in `deploy/README.md` — where the outbox is on the host, what the filenames mean, and why re-converting a document does not create a duplicate to ingest
- [X] T065 [US4] Return 404 with a clear message from `GET /api/jobs/{id}/markdown` in `src/pdf2md/api/jobs.py` when the operator has removed the file from the outbox, distinguishing "never produced" from "removed since"

**Checkpoint**: The handoff folder is a clean, duplicate-free record ready for manual import

---

## Phase 7: User Story 5 - Convert a batch of documents unattended (Priority: P3)

**Goal**: A user submits many PDFs at once and leaves; every document ends in a definite reported state, and one bad document never stalls the rest.

**Independent Test**: Upload a batch mixing valid and invalid PDFs, leave it unattended, and confirm every valid document converts, every invalid one is reported failed, and the batch does not stall. Restart mid-batch and confirm nothing is left at Converting.

### Tests for User Story 5

- [X] T066 [P] [US5] Integration test in `tests/integration/test_batch.py` — a mixed batch completes with each document individually reported, and one failure does not block the others (spec US5 scenario 1)
- [X] T067 [P] [US5] Integration test in `tests/integration/test_restart_recovery.py` — jobs in `queued`/`submitted`/`running` at restart are resubmitted with `attempt=2` when the inbox PDF is present, and marked `failed` with a restart reason when it is not; **nothing remains non-terminal** (spec US5 scenario 3)
- [X] T068 [P] [US5] Integration test in `tests/integration/test_timeout.py` — a job exceeding `JOB_TIMEOUT_SECONDS` becomes `timed_out` with a reason, the queue keeps moving, and no partial `.md` is written (FR-028)

### Implementation for User Story 5

- [X] T069 [US5] Extend `POST /api/uploads` in `src/pdf2md/api/uploads.py` to accept multiple `files` parts, create one `Batch` row, and report per-file acceptance without failing the whole batch (FR-009)
- [X] T070 [US5] Implement restart recovery in `src/pdf2md/dispatcher.py` startup — reset recoverable in-flight jobs to `queued` with `attempt+1`, and terminate unrecoverable ones as `failed`; never poll a stored `engine_task_id`, which does not survive an engine restart (contracts/docling-serve.md)
- [X] T071 [US5] Implement the timeout watchdog in `src/pdf2md/dispatcher.py` — transition to `timed_out` past `JOB_TIMEOUT_SECONDS`, set above the engine's own `MAX_DOCUMENT_TIMEOUT` so the engine normally gives up first
- [X] T072 [US5] Bound in-flight work in `src/pdf2md/dispatcher.py` — submit at most the engine's worker count plus a small buffer, so a 50-document batch queues rather than flooding the engine (FR-027)
- [X] T073 [US5] Track and expose `queue_position` in `src/pdf2md/dispatcher.py` and `src/pdf2md/api/jobs.py` from the engine's `task_position`, for display only
- [X] T074 [US5] Implement `since`-based incremental polling in `src/pdf2md/api/jobs.py` so a 50-row list polled every 2 seconds by several clients stays cheap (FR-010, SC-011)
- [X] T075 [US5] Add batch progress rendering to `src/pdf2md/static/app.js` — counts by state for the current batch, and a stable list that does not reorder while jobs complete
- [X] T076 [US5] Implement inbox reaping in `src/pdf2md/storage.py` — delete a PDF `INBOX_RETENTION_HOURS` after its job succeeds, and `FAILED_INBOX_RETENTION_DAYS` after a job ends `failed` or `timed_out`; never reap while any job for that content hash is non-terminal (data-model.md)
- [X] T095 [P] [US5] Unit test in `tests/unit/test_reaping.py` — succeeded PDFs are reaped on the short clock, failed PDFs survive it and are reaped on the long clock, and a PDF with a live job for the same content hash is never reaped
- [X] T077 [US5] Add backlog counts to `GET /api/jobs` and `GET /api/health` in `src/pdf2md/api/jobs.py` and `src/pdf2md/api/health.py` so a saturated stack is visible rather than merely slow

**Checkpoint**: A 50-document batch runs unattended to a definite outcome for every document

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Verification against the spec's own success criteria, and closing the open items from research

- [X] T078 Resolve research open item O1 in `deploy/docker-compose.yml` — confirm the engine's health endpoint path against its `/docs` and correct the healthcheck if `/health` is wrong
- [X] T079 Resolve research open item O2 — pin the exact verified `docling-serve-cpu` tag in `deploy/.env.example` and `deploy/README.md`, never `latest`, never a `-slim` variant
- [X] ~~T080~~ **OBSOLETE 2026-08-19, replaced by T110** — O3 concerned `pull_policy: never` through the Portainer UI; the stack now pulls from a registry (research.md R5)
- [X] T081 [P] Review every user-facing `failure_reason` string in `src/pdf2md/docling_client.py` for plain language — no stack traces, no engine jargon (FR-011)
- [ ] T082 [P] Run the V11 check from [quickstart.md](./quickstart.md) — load the page from a LAN client with its own internet disabled and a fresh profile; confirm the browser network tab shows requests only to the Mac mini (FR-025)
- [ ] T083 Run the V9 batch of 50 from quickstart.md while watching `docker stats`, and finalize `mem_limit` in `deploy/docker-compose.yml` from the measurement (SC-008, SC-011)
- [ ] T084 Measure SC-003 — convert a 20-page text PDF on the Mac mini and confirm it completes in under 3 minutes; record the figure in `deploy/README.md`
- [ ] T085 Run the full V1–V12 sequence from quickstart.md against the deployed stack and record results
- [ ] T089 [P] Assemble the fidelity corpus in `tests/fixtures/corpus/` — 20 representative complex PDFs spanning multi-column layout, tables, scanned pages, and figures, with `manifest.yaml` recording per document the expected heading count, table count, and figure count (SC-001, SC-002, FR-004)
- [X] T090 Build the fidelity harness in `ops/measure-fidelity.py` — convert every corpus document through the deployed stack, compare output against `manifest.yaml`, and report heading recall, table recall, figure presence and position, and first-attempt success rate
- [ ] T091 Run `ops/measure-fidelity.py` against the deployed stack and record results in `deploy/README.md` — gates: SC-001 ≥90% first-attempt success, SC-002 ≥95% headings and ≥90% tables, FR-004 figures present at correct positions with surrounding text intact
- [X] T086 [P] Write the top-level `README.md` — what the stack is, the two-service architecture, and pointers into `specs/001-docling-pdf2md-stack/`
- [ ] T087 [P] Verify SC-004 by timing a clean-host deploy from `deploy/README.md` alone (target: under 30 minutes) and correct any missing step in that file
- [ ] T088 Confirm SC-009 by importing the outbox into AnythingLLM — 10 spot-check questions, at least 9 citing the correct source document, no duplicates from re-converted files; record the result in `deploy/README.md`

---

## Phase 9: User Story 2 (revised) — Deploy from GitHub instead of an air-gapped archive

**Added 2026-08-19** from the clarification that the internet restriction binds the running tool, not the act of deploying it (spec Clarifications; FR-030, FR-031, FR-032; research.md R5, R10).

**Goal**: Portainer deploys the stack straight from this repository with no credential and no hand-carried archive, while the running containers stay exactly as sealed as before.

**Independent Test**: On a host that has never run this stack, point Portainer at the repository, supply two stack variables, and reach a healthy converting stack — without copying a file to the host or entering a credential. Then confirm both isolation scripts still pass.

**⚠️ Nothing in `networks:` changes.** If a task in this phase makes you edit that block, stop — the migration is about delivery, not topology (research.md R1).

### The repository itself

- [X] T096 [US2] Scan the entire git history for secrets before the repository is made public — `git log -p | grep -iE '(api[_-]?key|secret|token|password)\s*[:=]'` and confirm no commit of `deploy/.env.example` ever carried a value (FR-031). A hit means rotating the value, not merely deleting the line — **history is clean**: no secret-shaped assignment in any commit on any branch, and `deploy/.env.example` is the only env file ever committed, with `PDF2MD_ENGINE_API_KEY=` empty in every revision
- [X] T097 [US2] **Done — repository is public.** Make `github.com/MarRothm/pdf2md` public — it returns 404 unauthenticated today. This is what lets Portainer deploy with the Authentication toggle off and unlocks the free hosted `arm64` runners, which do not work in private repositories (FR-031, research.md R10)

### Build and publish the web image

- [X] T098 [P] [US2] Create `.github/workflows/ci.yml` — `ruff check src tests`, `ruff format --check src tests`, and `pytest` on every push and pull request
- [X] T099 [P] [US2] Create `.github/workflows/publish.yml` — on `v*` tags only, build `Dockerfile` natively on `ubuntu-24.04-arm` and push `ghcr.io/marrothm/pdf2md-web:<version>` using `GITHUB_TOKEN` with `permissions: packages: write`. No `latest` tag, and no build on ordinary commits (FR-032, research.md R10)
- [X] T100 [US2] **Done — `ghcr.io/marrothm/pdf2md-web:1.0.0` published and pulls anonymously (O6 resolved: it was created public).** Tag and push `v1.0.0`, then verify the package pulls with no credential — `docker logout ghcr.io && docker pull ghcr.io/marrothm/pdf2md-web:1.0.0`. A credential prompt means the package was created private; fix its visibility rather than storing a token (research.md O6)

### Stack definition

- [X] T101 [US2] In `deploy/docker-compose.yml`, replace `pull_policy: never` with `pull_policy: missing` on both services (research.md R5)
- [X] T102 [US2] **Done — both images pinned by digest** (`web` at `sha256:15ca84e9…`, verified `linux/arm64`). In `deploy/docker-compose.yml`, pin both images by tag **and** digest — `ghcr.io/docling-project/docling-serve-cpu:v1.18.0@sha256:6aa1b1428b5c83db2a4fc3431d99902ef115d9e1ce13eed0f716d23ed9d9a098`, and `ghcr.io/marrothm/pdf2md-web:1.0.0@sha256:<digest printed by T100>` (FR-032, contracts/stack.md)
- [X] T103 [P] [US2] Add `pyyaml` to the `dev` extra in `pyproject.toml` and write `tests/unit/test_compose_pins.py` — assert every image reference in `deploy/docker-compose.yml` carries an `@sha256:` digest, none is `latest` or a `-slim` variant, and no service sets `pull_policy: never` (FR-030, FR-032)
- [X] T104 [P] [US2] Update `deploy/.env.example` — `ENGINE_IMAGE` and `WEB_IMAGE` as digest-pinned GHCR references, and replace the air-gap commentary with what the operator now needs to know about pulls

### Ops scripts

- [X] T105 [P] [US2] Delete `ops/save-images.sh` and `ops/load-images.sh` — the transfer path they implement no longer exists, and a fallback nobody exercises is a fallback that fails when reached (research.md R5)
- [X] T106 [P] [US2] Write `ops/verify-engine-image.sh` — assert the engine image the stack is running matches the pinned digest, and that `/opt/app-root/src/.cache/docling/models` is populated. This is the check `save-images.sh` performed at export time, and it is the only thing standing between a `-slim` variant and a stack that deploys, reports healthy, and fails on the first scanned page (research.md R4)

### Operator documentation

- [X] T107 [US2] Rewrite `deploy/README.md` sections 1–3 and 8–9 — one-time preparation is now the outbox directory alone; deployment is Portainer's **Repository** method with the fields from [contracts/stack.md](./contracts/stack.md); the re-pull toggle warnings and image-transfer instructions go; GitOps updates stay off and the reason why is worth one sentence (FR-030, FR-032)
- [X] T108 [US2] Rewrite `deploy/PORTAINER-EE-CHECKLIST.md` around the Repository build method — Parts 0–1 collapse to creating the outbox directory, Part 3 selects Repository rather than Web editor, Part 5's toggles become Authentication off and GitOps off, and Part 7 gains the anonymous-pull and engine-digest checks. The file currently documents the retired path end to end
- [X] T109 [P] [US2] Update the root `README.md` — the `ops/` row no longer describes air-gap transfer, and the deployment sentence points at this repository as the source Portainer reads

### Verification

- [ ] T110 [US2] Resolve research open item O5 — deploy once through Portainer EE's Repository method and confirm the compose path resolves, the stack variables reach the `${...}` placeholders, no credential is requested, and a second redeploy reuses the pulled digests instead of re-downloading 4.4 GB. Record the result in `deploy/README.md`
- [ ] T111 [US3] Re-run `ops/verify-offline.sh` and `ops/verify-lan-only.sh` against the GitHub-deployed stack — the topology did not change, so both must still pass unchanged. A failure here means the migration altered the security posture (FR-021, FR-026)
- [ ] T112 [US2] Run `ops/verify-engine-image.sh` on the Mac mini against the pulled engine image, and record the digest match in `deploy/README.md` §11

**Checkpoint**: A clean host reaches a healthy stack from GitHub alone, with no credential and no archive, and both isolation checks still pass.

---

## Phase 10: Automatic splitting of over-long documents

**Added 2026-08-19** from the clarification that a document too long for the engine should be
split rather than refused (spec Clarifications; FR-033 through FR-037; research.md R11–R15).

**Goal**: A document of any length up to the ceiling converts unattended, and one large
enough to be unwieldy arrives in the outbox as citable section files rather than one wall of
Markdown.

**Independent Test**: Upload a PDF of at least ten times `PART_MAX_PAGES`; it converts to
completion with visible part progress and its output covers the whole document. Upload one
above `MAX_TOTAL_PAGES`; it is refused at upload in a second, for its length, not as damaged.

**⚠️ Two traps recorded in research, worth re-reading before starting**: the watchdog must
become per-part or every split document dies at 45 minutes (R12), and a part's Markdown must
be persisted in the same transaction that fetches it, because the engine serves each result
exactly once (R3, R14).

### Foundation for the phase

- [X] T113 [P] [US1] Add `pypdf` to the runtime dependencies in `pyproject.toml` — pure Python, no system libraries, so the arm64 image build is unaffected (research.md R11)
- [X] T114 [P] [US1] Add the six splitting settings to `src/pdf2md/config.py` with the defaults from [contracts/stack.md](./contracts/stack.md) — `PART_MAX_PAGES`, `MAX_TOTAL_PAGES`, `PARTS_IN_FLIGHT`, `SECTION_SPLIT_THRESHOLD_BYTES`, `SECTION_MIN_BYTES`, `SECTION_MAX_BYTES`
- [X] T115 [US1] Add the `conversion_part` table and the new `conversion_job` and `markdown_output` columns to `src/pdf2md/db.py`, with a migration that runs against an existing database — deployed stacks carry job history that must survive (FR-017)

### Reading and splitting the PDF

- [X] T116 [P] [US1] Write `tests/unit/test_pdfinfo.py` — page count of a well-formed PDF; an encrypted PDF raises something distinguishable from a damaged one; a truncated PDF raises a parse error; an extracted page range has exactly the pages asked for
- [X] T117 [US1] Implement `src/pdf2md/pdfinfo.py` — `page_count()` and `extract_range()` writing a page-range PDF into the inbox volume (research.md R11)

### Upload-time decision (FR-036)

- [X] T118 [P] [US1] Extend `tests/contract/test_uploads.py` — a document above `MAX_TOTAL_PAGES` is refused **at upload** for its length; an encrypted one for its password; an unreadable one as damaged; none of the three creates a job
- [X] T119 [US1] Read the page count in `src/pdf2md/api/uploads.py` and decide whole / split / refused there, storing `page_count` on the `SourceDocument`. The refusal for length MUST NOT use the "damaged" wording — that mislabelling is what prompted this feature

### Converting in parts (FR-034, FR-037)

- [X] T120 [P] [US1] Write `tests/integration/test_split.py` — a document over `PART_MAX_PAGES` produces the expected part count, every part converts, and one document's worth of output results
- [X] T121 [US1] Create `ConversionPart` rows and their page-range PDFs when a long document is queued, in `src/pdf2md/dispatcher.py`
- [X] T122 [US1] Submit parts respecting `PARTS_IN_FLIGHT` in `src/pdf2md/dispatcher.py`, so one long document cannot starve short ones behind it (research.md R14)
- [X] T123 [US1] Poll parts and persist each part's Markdown into its row **in the same transaction as the fetch** in `src/pdf2md/dispatcher.py` — the engine serves a result exactly once, so a part's output must be durable the moment it arrives (research.md R3)
- [X] T124 [US1] Join the parts' Markdown in `ordinal` order and write the output once every part is terminal, in `src/pdf2md/dispatcher.py`
- [X] T125 [US1] Change the watchdog in `src/pdf2md/dispatcher.py` from per-document to per-part (research.md R12). **A per-document watchdog terminates every split document** — this is not a tuning change
- [X] T126 [P] [US1] Extend `tests/integration/test_timeout.py` — a split document running longer than `JOB_TIMEOUT_SECONDS` in total is not timed out, while a single part exceeding it still is

### When one part fails (FR-035)

- [X] T127 [P] [US1] Write a test in `tests/integration/test_split.py` — one failing part yields `succeeded_incomplete`, the missing page range is named, output from the surviving parts is written, and the gap appears **in the Markdown file** as well as on the page
- [X] T128 [US1] Implement `succeeded_incomplete`, `missing_page_ranges`, and the in-file gap marker across `src/pdf2md/dispatcher.py`, `src/pdf2md/models.py`, and `src/pdf2md/db.py`

### Section files for the AnythingLLM handoff (FR-033)

- [X] T129 [P] [US4] Write `tests/unit/test_sectioning.py` — splits on the highest heading level actually present (not always `#`); sections below `SECTION_MIN_BYTES` merge; sections above `SECTION_MAX_BYTES` divide; names are deterministic and ordinals preserve reading order
- [X] T130 [US4] Implement `src/pdf2md/sectioning.py` (research.md R13)
- [X] T131 [US4] Write section files and one `MarkdownOutput` row each when the joined Markdown exceeds `SECTION_SPLIT_THRESHOLD_BYTES`, in `src/pdf2md/storage.py` and `src/pdf2md/dispatcher.py`; smaller documents keep producing exactly one file
- [X] T132 [US4] Delete the document's own previous section files before writing a new set, in `src/pdf2md/storage.py`, with a test asserting no other document's files are touched. This is the only outbox deletion the service performs and it exists because an engine upgrade can change heading detection (research.md R13)

### What the page shows (FR-037)

- [X] T133 [P] [US1] Extend `tests/contract/test_jobs.py` — the list payload carries `part_count`, `parts_completed`, and `missing_page_ranges`; the detail payload carries `outputs[]`
- [X] T134 [US1] Add those fields to `src/pdf2md/models.py` and `src/pdf2md/api/jobs.py` per [contracts/web-api.md](./contracts/web-api.md)
- [X] T135 [US1] Render *Converting — part 7 of 20* and *Converted — pages N–M are missing* in `src/pdf2md/static/app.js`, showing the part counter only when `part_count > 1` so ordinary documents look exactly as they do now

### Restart behaviour (FR-016, User Story 5)

- [X] T136 [P] [US5] Extend `tests/integration/test_restart_recovery.py` — a restart mid-split resubmits only the unfinished parts, and a part whose source PDF is gone fails the document while naming the page range
- [X] T137 [US5] Implement part-aware restart recovery in `src/pdf2md/dispatcher.py`

### Documentation and measurement

- [X] T138 [P] Document the six variables in `deploy/.env.example` and add a splitting section to `deploy/README.md` — what gets split, what gets refused, and that re-conversion replaces a document's section files
- [ ] T139 [P] Extend `ops/measure-fidelity.py` with `--seams`, scoring tables that span a part boundary separately from tables elsewhere (SC-013)
- [ ] T140 Resolve research open item O7 — measure seconds per page across the fidelity corpus and set `PDF2MD_PART_MAX_PAGES` from it; record the figure in `deploy/README.md` §11. The default of 100 is a guess with a safety factor
- [ ] T141 Resolve research open item O8 — run V17 and confirm seam damage stays inside SC-002's budget; if it does not, the boundary-selection escape hatch in research.md R15 becomes necessary
- [ ] T142 Run V13–V16 from [quickstart.md](./quickstart.md) against the deployed stack and record the results

**Checkpoint**: A 2000-page PDF converts unattended into citable section files, and a
document too long even for that is refused in a second with a reason that is true.

**Progress 2026-08-19**: the upload side and both new modules are built and green
(T113–T119, T129, T130). What remains is the dispatcher rework — T120–T128 and T131–T137 —
which turns one job into one job with N parts. That is a single coherent piece of work and
should be done in one go rather than in fragments: `fetch_and_persist` is where the
single-use result guarantee lives, and a half-converted dispatcher can appear to work while
silently dropping a part's Markdown.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: Depends on Foundational. No dependency on other stories
- **US2 (Phase 4)**: Depends on Foundational. Independently testable, but only demonstrable against a working conversion path, so US1 first is the practical order
- **US3 (Phase 5)**: Depends on Foundational, plus **T036** because it edits the compose file US2 creates
- **US4 (Phase 6)**: Depends on Foundational and on US1's persist step (T027)
- **US5 (Phase 7)**: Depends on Foundational and on US1's dispatcher (T026, T027)
- **Polish (Phase 8)**: Depends on the stories you intend to ship
- **Deployment migration (Phase 9)**: Depends on US2 and US3 being complete (they are). Within the phase: T096→T097 gate everything, T100 produces the digest T102 needs, and T110–T112 need the Mac mini
- **Splitting (Phase 10)**: Depends on US1 and US4. Within the phase: T113–T115 gate everything; T117 gates T119 and T121; T121→T122→T123→T124 are one sequence in `dispatcher.py`; T130 gates T131 and T132; T140–T142 need the deployed stack and the corpus

### Cross-story file conflicts

Three files are touched by more than one story. Sequence, do not parallelize, these:

| File | Touched by |
|---|---|
| `deploy/docker-compose.yml` | T036–T039, T045 (US2); T048–T051 (US3); T101–T102 (Phase 9) |
| `deploy/README.md` | T044, T054 (US2/US3); T107, T110, T112 (Phase 9); T138, T140 (Phase 10) |
| `src/pdf2md/dispatcher.py` (Phase 10) | T121–T125, T128, T131, T137 — one file, one sequence, no `[P]` between them |
| `src/pdf2md/dispatcher.py` | T026, T027, T033, T092 (US1); T058 (US4); T070–T073 (US5) |
| `src/pdf2md/static/app.js` | T032, T094 (US1); T062 (US4); T075 (US5) |
| `src/pdf2md/api/jobs.py` | T030, T031 (US1); T060, T065 (US4); T073, T074, T077 (US5) |

### Within Each User Story

Tests → engine client → dispatcher → endpoints → page. Write the tests first and confirm they fail.

---

## Parallel Execution Examples

**Phase 1 setup** — after T001 and T002:

```bash
Task: "Configure ruff and .gitignore in pyproject.toml"          # T003
Task: "Write Dockerfile for the web image"                        # T004
Task: "Configure pytest in pyproject.toml"                        # T005
Task: "Create .dockerignore"                                      # T006
```

**Phase 2 foundational** — after T007 and T009:

```bash
Task: "Define Pydantic models in src/pdf2md/models.py"            # T008
Task: "Implement src/pdf2md/storage.py"                           # T010
Task: "Implement src/pdf2md/naming.py"                            # T011
Task: "Configure logging in src/pdf2md/logging_config.py"         # T012
Task: "Create the page shell in src/pdf2md/static/"               # T014
Task: "Build the stub engine in tests/stubs/docling_stub.py"      # T015
```

**US1 tests** — all five in parallel, before any US1 implementation:

```bash
Task: "Contract test for POST /api/uploads"                       # T019
Task: "Contract test for GET /api/jobs and download"              # T020
Task: "Contract test for the engine client"                       # T021
Task: "Integration test for the convert flow"                     # T022
Task: "Integration test for bad input"                            # T023
```

**US2 ops scripts** — independent files:

```bash
Task: "Write ops/save-images.sh"                                  # T041
Task: "Write ops/load-images.sh"                                  # T042
Task: "Write deploy/.env.example"                                 # T043
Task: "Write deploy/README.md"                                    # T044
```

---

**Phase 9 migration** — the two workflows, the compose test, and the ops scripts touch different files:

```text
Task: "Create .github/workflows/ci.yml"                           # T098
Task: "Create .github/workflows/publish.yml"                      # T099
Task: "Add pyyaml + tests/unit/test_compose_pins.py"              # T103
Task: "Update deploy/.env.example"                                # T104
Task: "Delete ops/save-images.sh and ops/load-images.sh"          # T105
Task: "Write ops/verify-engine-image.sh"                          # T106
Task: "Update root README.md"                                     # T109
```

`deploy/README.md` (T107) and `deploy/PORTAINER-EE-CHECKLIST.md` (T108) are separate files and can also run in parallel with the above, but not with each other's content decisions — the checklist is the README's procedure as boxes, so write the README first.

---

**Phase 10 splitting** — independent files, safe together:

```text
Task: "Add pypdf to pyproject.toml"                               # T113
Task: "Add the six splitting settings to config.py"               # T114
Task: "Write tests/unit/test_pdfinfo.py"                          # T116
Task: "Extend tests/contract/test_uploads.py"                     # T118
Task: "Write tests/integration/test_split.py"                     # T120
Task: "Extend tests/integration/test_timeout.py"                  # T126
Task: "Write tests/unit/test_sectioning.py"                       # T129
Task: "Extend tests/contract/test_jobs.py"                        # T133
Task: "Extend tests/integration/test_restart_recovery.py"          # T136
Task: "Document the six variables in deploy/"                     # T138
Task: "Add --seams to ops/measure-fidelity.py"                    # T139
```

Everything else in Phase 10 lands in `dispatcher.py`, `storage.py`, or `db.py` and must run
in sequence. `dispatcher.py` in particular takes eight of the phase's tasks — that file is
the one the README calls the most failure-sensitive code in the repository, and parallel
edits to it are how the single-use result guarantee gets broken by accident.

---

## Implementation Strategy

### MVP scope

**Phase 1 + Phase 2 + Phase 3 (US1)** — T001–T033 plus T092–T094. This delivers a working conversion path: upload a complex PDF, watch it convert, download faithful Markdown. It runs under `uvicorn` against a local engine container, without the Portainer stack or the isolation topology.

That is the smallest genuinely useful increment, but note it is **not yet deployable on the Mac mini as specified** — the user's two hard constraints, Portainer deployment (US2) and offline/LAN-only isolation (US3), are both P1 and both still ahead. Treat US1+US2+US3 as the first shippable release; US1 alone is the first demonstrable one.

### Incremental delivery

1. Setup + Foundational → app shell with a durable schema
2. **US1** → conversion works end to end → demo
3. **US2** → deploys and survives reboots from Portainer → operator can run it
4. **US3** → isolation enforced and provable → **first release the spec would accept**
5. **US4** → clean, duplicate-free handoff folder for AnythingLLM
6. **US5** → unattended batches with restart recovery and timeouts
7. Polish → measured against the spec's success criteria

### Parallel team strategy

After Foundational: one developer takes US1 (the conversion path), a second takes US2+US3 together (they share `deploy/docker-compose.yml`, so one person should own that file). US4 and US5 both extend the dispatcher, so they are best done sequentially after US1 lands.

---

## Notes

- **Task IDs are stable identifiers, not file order.** T089–T095 were added during `/speckit-analyze` remediation and sit in the phases they belong to, so IDs are not strictly ascending top to bottom. Phase headings carry execution order.
- `[P]` means a different file with no incomplete dependency — check the cross-story conflict table before running tasks in parallel
- Every task names its file path; no task requires reading another task to know where the code goes
- The dispatcher's fetch-and-persist step (T027) is the single most failure-sensitive task in the list — the engine serves each result exactly once
- Commit after each task or logical group; stop at any checkpoint to validate the story independently

---

## Implementation status

Everything that can be built and verified without the deployed stack is done: the web
service, the page, the Portainer stack definition, the ops scripts, and 167 unit,
contract, and integration tests against a stub engine.

**Phase 9 is 11 of 17 done.** The 2026-08-19 clarifications replaced the air-gapped
delivery path with deployment from GitHub. Everything that can be changed in this
repository has been: the compose file pins and pull policy, both CI workflows, the
compose-pinning test, `ops/verify-engine-image.sh`, both operator documents, and the
removal of the two transfer scripts. None of the web service's own code was affected —
the containers are as sealed as they ever were, and `ops/verify-offline.sh` and
`ops/verify-lan-only.sh` are unchanged and still the proof.

What remains needs decisions or hardware that a repository edit cannot supply:

| Task | Needs |
|---|---|
| T110, T112 | The Mac mini: a Repository-method deploy (O5) and the engine digest check |
| T111 | The deployed stack, to re-run both isolation scripts |
| T113–T139 | Nothing — buildable and testable against the stub engine |
| T140, T141 | The fidelity corpus and the deployed stack, to set `PART_MAX_PAGES` from measurement (O7) and confirm the seam tradeoff (O8) |
| T142 | The deployed stack, for V13–V16 |

T097, T100, and T102 closed on 2026-08-19: the repository is public,
`ghcr.io/marrothm/pdf2md-web:1.0.0` is published and pulls with no credential (which
resolves open item O6 — the package was created public), and both images are now pinned
by digest. Two defects surfaced on the way and are fixed in `.github/workflows/publish.yml`:
`docker/build-push-action` needs a buildx builder to honour `platforms:`, and
`github.repository_owner` is `MarRothm` while OCI repository names must be lowercase.

Two research open items were resolved from the pinned engine tag rather than left to the
first deploy: **O1** (the health path is `/ready`, which gates on model loading — `/health`
only reports the process is up) and **O2** (`v1.18.0`, arm64 digest recorded in
`deploy/.env.example`). **O4** was decided: `partial_success` counts as converted and is
surfaced distinctly.

The tasks still unchecked all need the Mac mini with both images loaded and the stack
deployed — they are measurements and confirmations, not unwritten code:

| Task | Needs |
|---|---|
| T100, T110, T112 | The GitHub deployment migration (Phase 9) — a published package, then a Repository-method deploy and the engine digest check |
| T082 | A LAN client with its own internet disabled, checking the browser network tab (V11) |
| T083 | `docker stats` during a 50-document batch, to finalize `DOCLING_MEM_LIMIT` (currently a documented starting value of 5g) |
| T084 | A 20-page PDF converted on the Mac mini, timed (SC-003) |
| T085 | The full V1–V12 sequence from quickstart.md against the deployed stack |
| T087 | A timed clean-host deploy from `deploy/README.md` alone (SC-004) |
| T088 | An AnythingLLM import and 10 spot-check questions (SC-009) |
| T089 | Twenty real complex PDFs. `tests/fixtures/corpus/` holds the manifest schema, the trait coverage the set must have, and instructions; the documents themselves have to be chosen from what this workgroup actually converts |
| T091 | T089 plus the deployed stack, then `ops/measure-fidelity.py` (the harness itself is written and exercised) |

`deploy/README.md` §11 is the table to record those measurements in.
