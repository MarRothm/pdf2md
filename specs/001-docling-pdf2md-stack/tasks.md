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

- [ ] T001 Create the source tree from plan.md — `src/pdf2md/`, `src/pdf2md/api/`, `src/pdf2md/static/`, `tests/{unit,contract,integration}/`, `deploy/`, `ops/` — each with a `.gitkeep` or `__init__.py` as appropriate
- [ ] T002 Create `pyproject.toml` declaring Python 3.12, runtime deps (`fastapi`, `uvicorn[standard]`, `httpx`, `python-multipart`, `pydantic`, `pydantic-settings`) and a `dev` extra (`pytest`, `pytest-asyncio`, `ruff`)
- [ ] T003 [P] Configure `ruff` lint and format rules in `pyproject.toml` and add `.gitignore` covering `.venv/`, `__pycache__/`, `*.sqlite*`, `deploy/.env`
- [ ] T004 [P] Write `Dockerfile` for the web image — `python:3.12-slim`, `linux/arm64`, non-root user, no build toolchain in the final layer, `CMD` running uvicorn on port 8080
- [ ] T005 [P] Configure pytest in `pyproject.toml` — `asyncio_mode=auto`, testpaths, and markers `contract`, `integration`, `unit`
- [ ] T006 [P] Create `.dockerignore` excluding `tests/`, `specs/`, `.venv/`, and `deploy/.env` from the build context

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Configuration, persistence, and the app shell that every user story builds on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T007 Implement settings in `src/pdf2md/config.py` — every `PDF2MD_*` variable from [contracts/stack.md](./contracts/stack.md) with its documented default, loaded via `pydantic-settings`, failing fast on a missing `PDF2MD_ENGINE_API_KEY`
- [ ] T008 [P] Define Pydantic models in `src/pdf2md/models.py` for `Batch`, `SourceDocument`, `ConversionJob`, `MarkdownOutput`, the `JobStatus` enum, and the API payload shapes from [contracts/web-api.md](./contracts/web-api.md)
- [ ] T009 Implement `src/pdf2md/db.py` — the full DDL from [data-model.md](./data-model.md), a migration runner, and a connection helper setting `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` on every connection
- [ ] T010 [P] Implement `src/pdf2md/storage.py` — inbox/outbox path resolution, an atomic write helper (temp file → `fsync` → rename), free-space checks, and a writability probe used by health
- [ ] T011 [P] Implement `src/pdf2md/naming.py` — `output_filename(original_name, content_hash) -> "{slug}--{hash12}.md"` per [research.md](./research.md) R8, with filename sanitization and slug collision-free behavior
- [ ] T012 [P] Configure structured logging in `src/pdf2md/logging_config.py` — every log line for a job carries job id, source filename, and outcome so Portainer logs satisfy FR-019
- [ ] T013 Implement the app factory in `src/pdf2md/main.py` — FastAPI instance, lifespan running migrations and starting the dispatcher, static mount at `/`, and `GET /healthz` per [contracts/web-api.md](./contracts/web-api.md)
- [ ] T014 [P] Create the page shell `src/pdf2md/static/index.html` and `src/pdf2md/static/styles.css` — self-hosted assets only, no CDN link, no external font, no analytics (FR-025)
- [ ] T015 [P] Build a stub `docling-serve` in `tests/stubs/docling_stub.py` implementing the async task API from [contracts/docling-serve.md](./contracts/docling-serve.md), including single-use result semantics and injectable failure/delay modes
- [ ] T016 Create `tests/conftest.py` — temp inbox/outbox/db fixtures, an ASGI client fixture, and a fixture wiring the app to the stub engine from T015
- [ ] T017 [P] Unit tests for naming in `tests/unit/test_naming.py` — same bytes give the same name, different bytes under one filename give different names, hostile filenames are sanitized
- [ ] T018 [P] Unit tests for schema and settings in `tests/unit/test_db.py` and `tests/unit/test_config.py` — migrations are idempotent, the status CHECK constraint rejects unknown states, defaults match contracts/stack.md

**Checkpoint**: App starts, serves `/healthz`, and has a durable schema — user stories can now begin

---

## Phase 3: User Story 1 - Convert a complex PDF into ingestion-ready Markdown (Priority: P1) 🎯 MVP

**Goal**: A LAN user uploads a complex PDF through the browser page, watches it convert, and retrieves faithful Markdown — multi-column reading order, tables, and text recovered from scanned pages.

**Independent Test**: Upload a PDF containing a multi-column page, a table, and a scanned page; confirm the Markdown has correct reading order, a Markdown table, and recognized text from the scan. Runs against the stub engine for logic, and against the real engine for fidelity.

### Tests for User Story 1

- [ ] T019 [P] [US1] Contract test for `POST /api/uploads` in `tests/contract/test_uploads.py` — 202 shape, `accepted`/`rejected` split, rejection reasons for non-PDF, zero-byte, and oversized files
- [ ] T020 [P] [US1] Contract test for `GET /api/jobs`, `GET /api/jobs/{id}`, and the Markdown download in `tests/contract/test_jobs.py` — field presence, `display_status` mapping, `download_url` only on success, `Content-Disposition` filename
- [ ] T021 [P] [US1] Contract test for the engine client in `tests/contract/test_docling_client.py` — request shape sent to `/v1/convert/file/async`, `X-Api-Key` header, and parsing of poll and result payloads
- [ ] T022 [P] [US1] Integration test in `tests/integration/test_convert_flow.py` — upload → queued → running → succeeded → Markdown present in the outbox and downloadable, driven by the stub engine
- [ ] T023 [P] [US1] Integration test in `tests/integration/test_bad_input.py` — corrupt and password-protected PDFs end as `failed` with a human-readable reason and **no** file in the outbox (FR-007, spec US1 scenario 4)

### Implementation for User Story 1

- [ ] T024 [US1] Implement the engine client in `src/pdf2md/docling_client.py` — async submit/poll/result against the endpoints in [contracts/docling-serve.md](./contracts/docling-serve.md), sending `from_formats=pdf`, `to_formats=md`, `do_ocr=true` explicitly, with the API key header
- [ ] T025 [US1] Implement engine-to-job status mapping in `src/pdf2md/docling_client.py` — the mapping table in contracts/docling-serve.md, translating engine `errors[]` into non-technical `failure_reason` text while preserving raw detail in `engine_errors`
- [ ] T026 [US1] Implement the dispatcher loop in `src/pdf2md/dispatcher.py` — claim `queued` jobs, submit, poll at `POLL_INTERVAL_SECONDS`, and drive state transitions per the [data-model.md](./data-model.md) state machine
- [ ] T027 [US1] Implement fetch-and-persist as one indivisible step in `src/pdf2md/dispatcher.py` — call `/v1/result/{task_id}` exactly once, write Markdown atomically to the outbox, then commit `MarkdownOutput` and `succeeded` in a single transaction; any failure after the fetch marks the job `failed` with a reason naming the lost result (research.md R3)
- [ ] T028 [US1] Implement PDF validation in `src/pdf2md/api/uploads.py` — magic-byte check, size limit, zero-byte rejection, SHA-256 hashing while streaming to the inbox so a document is never held in memory in full
- [ ] T029 [US1] Implement `POST /api/uploads` in `src/pdf2md/api/uploads.py` returning the 202 payload from contracts/web-api.md, creating `SourceDocument` and `ConversionJob` rows
- [ ] T030 [US1] Implement `GET /api/jobs` and `GET /api/jobs/{id}` in `src/pdf2md/api/jobs.py`, including `display_status` derivation so the page never maps states itself
- [ ] T031 [US1] Implement `GET /api/jobs/{id}/markdown` in `src/pdf2md/api/jobs.py` — `text/markdown`, `Content-Disposition` filename identical to the outbox filename, 409 while still converting, 404 when absent (FR-012)
- [ ] T032 [US1] Implement the page behavior in `src/pdf2md/static/app.js` — file picker, upload, a status list polling `GET /api/jobs`, per-job failure reason, and a download link on success; vanilla ES2022, no framework, no external request
- [ ] T033 [US1] Add per-job logging in `src/pdf2md/dispatcher.py` — submission, each state change, and outcome, so a failure is diagnosable from Portainer logs alone (FR-019)
- [ ] T092 [US1] Implement suspect-yield detection in `src/pdf2md/dispatcher.py` — compute characters-per-page against `SUSPECT_MIN_CHARS_PER_PAGE` during the persist step (T027) and set `succeeded_suspect` instead of `succeeded`; the Markdown is written either way (FR-029)
- [ ] T093 [P] [US1] Integration test in `tests/integration/test_suspect_yield.py` — a blank-scan PDF and a near-empty result both report `succeeded_suspect` with the output still downloadable, and a normal document does not trip the threshold (FR-029)
- [ ] T094 [US1] Render `succeeded_suspect` distinctly in `src/pdf2md/static/app.js` — visually separated from plain success, with the download still offered (FR-029)

**Checkpoint**: A single complex PDF converts end to end and is retrievable — this is the MVP

---

## Phase 4: User Story 2 - Operate the stack from Portainer on the Mac mini (Priority: P1)

**Goal**: An operator deploys, redeploys, and inspects the whole stack from Portainer, and it survives a Mac mini reboot unattended.

**Independent Test**: Deploy the stack in Portainer from the provided definition with no host-side steps beyond creating the outbox directory; confirm both services report healthy, redeploy and confirm outputs and history persist, reboot the Mac mini and confirm unattended recovery.

### Tests for User Story 2

- [ ] T034 [P] [US2] Contract test for `GET /api/health` in `tests/contract/test_health.py` — the payload from contracts/web-api.md, 503 with `"degraded"` when the engine is unreachable, and uploads still accepted while degraded
- [ ] T035 [P] [US2] Integration test in `tests/integration/test_persistence.py` — job history and outbox contents survive a simulated restart of the app against the same volumes (FR-017)

### Implementation for User Story 2

- [ ] T036 [US2] Write `deploy/docker-compose.yml` — the `web` and `docling` services from [contracts/stack.md](./contracts/stack.md) with pinned image tags, `pull_policy: never`, and `restart: unless-stopped` (FR-016). Networks are added by US3
- [ ] T037 [US2] Declare volumes in `deploy/docker-compose.yml` — named `db` and `inbox` volumes, and the `${OUTBOX_HOST_PATH}` bind mount; the SQLite database must not be on a bind mount (research.md R7)
- [ ] T038 [US2] Set every `docling` environment variable from contracts/stack.md in `deploy/docker-compose.yml` — worker count, `SHARE_MODELS`, timeouts, file and page limits, `DOCLING_DEVICE=cpu`, queue size (FR-027, FR-028)
- [ ] T039 [US2] Add healthchecks and `depends_on: docling: condition: service_healthy` in `deploy/docker-compose.yml` so Portainer reports true health and the page never starts against a cold engine (FR-018). Confirm the engine's health path against `/docs` first (research.md O1)
- [ ] T040 [US2] Implement `GET /api/health` in `src/pdf2md/api/health.py` — engine reachability, backlog counts, outbox writability and free space, database writability
- [ ] T041 [P] [US2] Write `ops/save-images.sh` — pull the pinned engine tag for `linux/arm64`, **verify `/opt/app-root/src/.cache/docling/models` is populated and abort if not**, build the web image, `docker save | gzip` both, print SHA-256 checksums (research.md R4, R5)
- [ ] T042 [P] [US2] Write `ops/load-images.sh` — verify checksums, `docker load` both archives, assert both pinned tags are present locally
- [ ] T043 [P] [US2] Write `deploy/.env.example` with every tunable and its chosen default, and no secret values
- [ ] T044 [P] [US2] Write `deploy/README.md` — Portainer deploy and redeploy steps, the "re-pull image toggle stays OFF" warning, and the storage locations with their purpose and expected growth (FR-020)
- [ ] T045 [US2] Set `mem_limit` on both services in `deploy/docker-compose.yml` from measured engine RSS at 2 workers with `SHARE_MODELS=true`, leaving headroom for Portainer on the 8.38 GB VM (SC-011, research.md R6)

**Checkpoint**: The stack deploys and redeploys from Portainer alone and survives a reboot

---

## Phase 5: User Story 3 - Guarantee the stack is offline and LAN-only (Priority: P1)

**Goal**: Isolation is a structural property of the stack definition, and the operator can prove it on demand.

**Independent Test**: With the host's internet path blocked, deploy from scratch and convert a document end to end; separately confirm the page answers on the LAN and is unreachable from outside it.

**⚠️ Depends on T036** — US3 edits the compose file US2 creates. If US3 is done first, create `deploy/docker-compose.yml` here instead.

### Tests for User Story 3

- [ ] T046 [P] [US3] Test in `tests/unit/test_static_assets.py` that no file under `src/pdf2md/static/` references an external origin — scan for `http://`, `https://`, and `//` URLs in `index.html`, `app.js`, and `styles.css` (FR-025)
- [ ] T047 [P] [US3] Test in `tests/unit/test_no_egress_paths.py` that the codebase makes no outbound call other than to `PDF2MD_ENGINE_URL` — assert every `httpx` base URL originates from settings

### Implementation for User Story 3

- [ ] T048 [US3] Add the two networks to `deploy/docker-compose.yml` — `core` with `internal: true`, and `edge` as a bridge with `com.docker.network.bridge.enable_ip_masquerade: "false"` (research.md R1)
- [ ] T049 [US3] Attach services in `deploy/docker-compose.yml` — `web` on both `edge` and `core` with only its port published; `docling` on `core` alone with no ports (FR-023)
- [ ] T050 [US3] Set the isolation environment variables on `docling` in `deploy/docker-compose.yml` — `ARTIFACTS_PATH`, `LOAD_MODELS_AT_BOOT=true`, `ENABLE_REMOTE_SERVICES=false`, `ALLOW_EXTERNAL_PLUGINS=false`, `ENABLE_UI=false` (FR-021, FR-022)
- [ ] T051 [US3] Wire `DOCLING_SERVE_API_KEY` from the Portainer stack variable in `deploy/docker-compose.yml` and send it from the client, keeping the web service itself credential-free for users (FR-024, research.md R9)
- [ ] T052 [P] [US3] Write `ops/verify-offline.sh` — assert engine egress fails, web egress fails, and `web → docling:5001` succeeds; then run a real conversion and assert no download errors in the engine log (FR-026)
- [ ] T053 [P] [US3] Write `ops/verify-lan-only.sh` — assert HTTP 200 from the Mac mini's LAN address, assert the `docling` service publishes no ports, and prompt the operator to confirm no router port-forward maps the port (FR-026)
- [ ] T054 [P] [US3] Document the isolation model and both verification procedures in `deploy/README.md` — including the warning that consolidating onto one `internal` network breaks published ports (research.md R1)
- [ ] T055 [US3] Add a startup assertion in `src/pdf2md/main.py` that `PDF2MD_ENGINE_URL` resolves to a private address, refusing to start against a public host

**Checkpoint**: Isolation is enforced by the stack file and provable with two scripts

---

## Phase 6: User Story 4 - Collect converted Markdown for import into AnythingLLM (Priority: P2)

**Goal**: Every successful conversion lands in one outbox folder under a stable, unique, traceable name, ready for the operator's manual AnythingLLM import.

**Independent Test**: Convert a small set of PDFs, confirm each appears in the outbox with a `{slug}--{hash12}.md` name and downloads from the page under the identical name, then import the folder into AnythingLLM and confirm answers cite the right documents.

### Tests for User Story 4

- [ ] T056 [P] [US4] Integration test in `tests/integration/test_dedup.py` — converting identical bytes twice yields one outbox file, the second job reports `already_converted`, and a renamed copy of the same bytes resolves to the same output (FR-014, spec US4 scenario 3)
- [ ] T057 [P] [US4] Integration test in `tests/integration/test_outbox.py` — no file appears for a failed job; an interrupted write leaves no truncated `.md`; download filename equals the outbox filename

### Implementation for User Story 4

- [ ] T058 [US4] Implement the `already_converted` path in `src/pdf2md/dispatcher.py` — on claiming a `queued` job, if a `MarkdownOutput` exists for the content hash **and** the file is present in the outbox, terminate the job as `already_converted` with the existing `output_filename` and do no engine work
- [ ] T059 [US4] Persist `MarkdownOutput` rows in `src/pdf2md/db.py` with `engine_status` recording `success` versus `partial_success`, and surface `partial_success` distinctly rather than as plain success (research.md O4)
- [ ] T060 [US4] Implement `POST /api/jobs/{id}/retry` in `src/pdf2md/api/jobs.py` — creates a new job for the same source, 409 when the original succeeded or the inbox PDF was already reaped
- [ ] T061 [US4] Implement the outbox inventory query in `src/pdf2md/db.py` and expose it in `GET /api/health` as a document count, giving the operator an import checklist (data-model.md derived views)
- [ ] T062 [US4] Show `already_converted` and `partial_success` distinctly in `src/pdf2md/static/app.js`, including the existing output filename so the user knows where the document already is
- [ ] T063 [US4] Implement history pruning in `src/pdf2md/db.py` — prune jobs older than `JOB_HISTORY_DAYS` while **never** deleting `markdown_output` rows or outbox files (data-model.md)
- [ ] T064 [US4] Document the manual AnythingLLM import step in `deploy/README.md` — where the outbox is on the host, what the filenames mean, and why re-converting a document does not create a duplicate to ingest
- [ ] T065 [US4] Return 404 with a clear message from `GET /api/jobs/{id}/markdown` in `src/pdf2md/api/jobs.py` when the operator has removed the file from the outbox, distinguishing "never produced" from "removed since"

**Checkpoint**: The handoff folder is a clean, duplicate-free record ready for manual import

---

## Phase 7: User Story 5 - Convert a batch of documents unattended (Priority: P3)

**Goal**: A user submits many PDFs at once and leaves; every document ends in a definite reported state, and one bad document never stalls the rest.

**Independent Test**: Upload a batch mixing valid and invalid PDFs, leave it unattended, and confirm every valid document converts, every invalid one is reported failed, and the batch does not stall. Restart mid-batch and confirm nothing is left at Converting.

### Tests for User Story 5

- [ ] T066 [P] [US5] Integration test in `tests/integration/test_batch.py` — a mixed batch completes with each document individually reported, and one failure does not block the others (spec US5 scenario 1)
- [ ] T067 [P] [US5] Integration test in `tests/integration/test_restart_recovery.py` — jobs in `queued`/`submitted`/`running` at restart are resubmitted with `attempt=2` when the inbox PDF is present, and marked `failed` with a restart reason when it is not; **nothing remains non-terminal** (spec US5 scenario 3)
- [ ] T068 [P] [US5] Integration test in `tests/integration/test_timeout.py` — a job exceeding `JOB_TIMEOUT_SECONDS` becomes `timed_out` with a reason, the queue keeps moving, and no partial `.md` is written (FR-028)

### Implementation for User Story 5

- [ ] T069 [US5] Extend `POST /api/uploads` in `src/pdf2md/api/uploads.py` to accept multiple `files` parts, create one `Batch` row, and report per-file acceptance without failing the whole batch (FR-009)
- [ ] T070 [US5] Implement restart recovery in `src/pdf2md/dispatcher.py` startup — reset recoverable in-flight jobs to `queued` with `attempt+1`, and terminate unrecoverable ones as `failed`; never poll a stored `engine_task_id`, which does not survive an engine restart (contracts/docling-serve.md)
- [ ] T071 [US5] Implement the timeout watchdog in `src/pdf2md/dispatcher.py` — transition to `timed_out` past `JOB_TIMEOUT_SECONDS`, set above the engine's own `MAX_DOCUMENT_TIMEOUT` so the engine normally gives up first
- [ ] T072 [US5] Bound in-flight work in `src/pdf2md/dispatcher.py` — submit at most the engine's worker count plus a small buffer, so a 50-document batch queues rather than flooding the engine (FR-027)
- [ ] T073 [US5] Track and expose `queue_position` in `src/pdf2md/dispatcher.py` and `src/pdf2md/api/jobs.py` from the engine's `task_position`, for display only
- [ ] T074 [US5] Implement `since`-based incremental polling in `src/pdf2md/api/jobs.py` so a 50-row list polled every 2 seconds by several clients stays cheap (FR-010, SC-011)
- [ ] T075 [US5] Add batch progress rendering to `src/pdf2md/static/app.js` — counts by state for the current batch, and a stable list that does not reorder while jobs complete
- [ ] T076 [US5] Implement inbox reaping in `src/pdf2md/storage.py` — delete a PDF `INBOX_RETENTION_HOURS` after its job succeeds, and `FAILED_INBOX_RETENTION_DAYS` after a job ends `failed` or `timed_out`; never reap while any job for that content hash is non-terminal (data-model.md)
- [ ] T095 [P] [US5] Unit test in `tests/unit/test_reaping.py` — succeeded PDFs are reaped on the short clock, failed PDFs survive it and are reaped on the long clock, and a PDF with a live job for the same content hash is never reaped
- [ ] T077 [US5] Add backlog counts to `GET /api/jobs` and `GET /api/health` in `src/pdf2md/api/jobs.py` and `src/pdf2md/api/health.py` so a saturated stack is visible rather than merely slow

**Checkpoint**: A 50-document batch runs unattended to a definite outcome for every document

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Verification against the spec's own success criteria, and closing the open items from research

- [ ] T078 Resolve research open item O1 in `deploy/docker-compose.yml` — confirm the engine's health endpoint path against its `/docs` and correct the healthcheck if `/health` is wrong
- [ ] T079 Resolve research open item O2 — pin the exact verified `docling-serve-cpu` tag in `deploy/.env.example` and `deploy/README.md`, never `latest`, never a `-slim` variant
- [ ] T080 Resolve research open item O3 — deploy once through the Portainer UI specifically and confirm `pull_policy: never` is honored; record the result and any fallback in `deploy/README.md`
- [ ] T081 [P] Review every user-facing `failure_reason` string in `src/pdf2md/docling_client.py` for plain language — no stack traces, no engine jargon (FR-011)
- [ ] T082 [P] Run the V11 check from [quickstart.md](./quickstart.md) — load the page from a LAN client with its own internet disabled and a fresh profile; confirm the browser network tab shows requests only to the Mac mini (FR-025)
- [ ] T083 Run the V9 batch of 50 from quickstart.md while watching `docker stats`, and finalize `mem_limit` in `deploy/docker-compose.yml` from the measurement (SC-008, SC-011)
- [ ] T084 Measure SC-003 — convert a 20-page text PDF on the Mac mini and confirm it completes in under 3 minutes; record the figure in `deploy/README.md`
- [ ] T085 Run the full V1–V12 sequence from quickstart.md against the deployed stack and record results
- [ ] T089 [P] Assemble the fidelity corpus in `tests/fixtures/corpus/` — 20 representative complex PDFs spanning multi-column layout, tables, scanned pages, and figures, with `manifest.yaml` recording per document the expected heading count, table count, and figure count (SC-001, SC-002, FR-004)
- [ ] T090 Build the fidelity harness in `ops/measure-fidelity.py` — convert every corpus document through the deployed stack, compare output against `manifest.yaml`, and report heading recall, table recall, figure presence and position, and first-attempt success rate
- [ ] T091 Run `ops/measure-fidelity.py` against the deployed stack and record results in `deploy/README.md` — gates: SC-001 ≥90% first-attempt success, SC-002 ≥95% headings and ≥90% tables, FR-004 figures present at correct positions with surrounding text intact
- [ ] T086 [P] Write the top-level `README.md` — what the stack is, the two-service architecture, and pointers into `specs/001-docling-pdf2md-stack/`
- [ ] T087 [P] Verify SC-004 by timing a clean-host deploy from `deploy/README.md` alone (target: under 30 minutes) and correct any missing step in that file
- [ ] T088 Confirm SC-009 by importing the outbox into AnythingLLM — 10 spot-check questions, at least 9 citing the correct source document, no duplicates from re-converted files; record the result in `deploy/README.md`

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

### Cross-story file conflicts

Three files are touched by more than one story. Sequence, do not parallelize, these:

| File | Touched by |
|---|---|
| `deploy/docker-compose.yml` | T036–T039, T045 (US2); T048–T051 (US3) |
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
