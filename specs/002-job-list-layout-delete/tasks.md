---
description: "Task list for 002-job-list-layout-delete"
---

# Tasks: Fixed-Width Document List with Detail on Demand and Conversion Deletion

**Input**: Design documents from `/specs/002-job-list-layout-delete/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Included. The plan names three test files and the repository already gates every change on `pytest` and `ruff`; the layout requirements have no other verification (research.md R12).

**Organization**: Grouped by user story so each ships on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 (fixed layout), US2 (deletion), US3 (detail dialog)

## Path Conventions

Single project at repository root: `src/pdf2md/`, `tests/`. Page assets live in
`src/pdf2md/static/`. Paths below are exact.

---

## Phase 1: Setup

**Purpose**: A known-good starting point and the one test helper both deletion suites need.

- [X] T001 Confirm the baseline is green before touching anything: `pytest` and `ruff check src tests && ruff format --check src tests` from the repository root, against the suite in `tests/`
- [X] T002 [P] Add a `converted_document` helper to `tests/conftest.py` that uploads a PDF, drives the stub engine to completion, and returns the job id, content hash, and the outbox paths it wrote — the fixture T011–T013 build on

---

## Phase 2: Foundational

**Purpose**: The modal primitive that the confirmation (US2) and the detail view (US3) both open.

**Note on blocking**: this phase does **not** block User Story 1. US1 touches only the table's
layout and can be built, tested, and shipped while this phase is untouched. US2 and US3 both
depend on it.

- [X] T003 Add a reusable modal `<dialog>` element to `src/pdf2md/static/index.html`, labelled by its heading, with a close button and an empty body the callers fill
- [X] T004 [P] Style the dialog and its `::backdrop` in `src/pdf2md/static/styles.css`, using the existing custom properties — no new colour values
- [X] T005 [P] Add `openModal(content, opener)` and `closeModal()` helpers to `src/pdf2md/static/app.js` using `showModal()`/`close()`, so Escape, the backdrop, and the close button all dismiss and focus returns to the opener (contracts/page-layout.md D3)

**Checkpoint**: A dialog opens, closes, and returns focus. US2 and US3 can now begin.

---

## Phase 3: User Story 1 - Read the document list without fighting the page (Priority: P1) 🎯 MVP

**Goal**: The documents list stays inside the window whatever the messages say. Fixed columns,
three-line preview, actions always reachable.

**Independent Test**: Submit a batch with one long failure, one page-gap caution, and one waiting
document. No horizontal scrolling at any width; column boundaries identical before and after the
batch completes; every finished row's download reachable without scrolling sideways.

### Tests for User Story 1

- [X] T006 [P] [US1] Create `tests/unit/test_page_layout.py` asserting invariants L1–L5 from `contracts/page-layout.md` against the three assets: `table-layout: fixed` declared for `table.jobs`, a width on every `<th scope="col">`, `overflow-wrap: anywhere` on the text cells, a line clamp declared, and **no** `white-space: nowrap` anywhere in `styles.css`. Follow the asset-reading style of `tests/unit/test_static_assets.py`

### Implementation for User Story 1

- [X] T007 [US1] In `src/pdf2md/static/styles.css`, put `table.jobs` on `table-layout: fixed`, give each column its width from the contract table (Document 30%, Status 15%, Detail 40%, Actions 15%), and add `overflow-wrap: anywhere` to the text cells
- [X] T008 [US1] In `src/pdf2md/static/styles.css`, delete `white-space: nowrap` from `.state` — the status column wraps like every other (L3) — and add a `.clamp` class implementing the three-line clamp with both `-webkit-line-clamp` and `line-clamp` (research.md R2)
- [X] T009 [US1] In `src/pdf2md/static/index.html`, rename the fourth column header from `Markdown` to `Actions`
- [X] T010 [US1] In `renderDetail` in `src/pdf2md/static/app.js`, apply the `.clamp` class to the detail cell and, after insertion, add a "More" `<button>` when `scrollHeight > clientHeight`. The button is inert until T030 wires it to the dialog; leave it disabled with a `title` until then rather than wiring it to nothing
- [X] T011 [US1] Rename `renderDownload` to `renderActions` in `src/pdf2md/static/app.js`: keep the download anchor but label it `Download` with the output filename moved to its `title`, and return a cell that stacks its controls (contracts/page-layout.md, Actions cell)
- [X] T012 [US1] In `renderRow` in `src/pdf2md/static/app.js`, set the filename cell's `title` to the full filename so a wrapped name stays readable (FR-007)

**Checkpoint**: The reported defect is fixed and shippable on its own. Run quickstart scenarios 1–2.

---

## Phase 4: User Story 2 - Delete a conversion and its output, after confirming (Priority: P2)

**Goal**: An operator removes a document and everything it produced, after a confirmation that
names exactly what will go.

**Independent Test**: Convert, delete, confirm the dialog names the document and lists its files;
verify the Markdown, the retained upload, and every list entry are gone, that no other document
was touched, and that re-uploading the same PDF converts afresh. Cancel and verify nothing moved.

### Tests for User Story 2

- [X] T013 [P] [US2] Create `tests/contract/test_deletion.py` covering `contracts/web-api-deletion.md`: `DELETE /api/jobs/{job_id}` 200 shape, 409 `still_converting`, 404 `already_deleted`, the `content_hash` filter on `GET /api/jobs`, `content_hash` present on every `JobSummary`, and — the case that matters — an `already_converted` job returning an empty `outputs` alongside a populated `document_outputs`
- [X] T014 [P] [US2] Create `tests/integration/test_delete_flow.py` for the journeys: split document with several section files, two conversions of one PDF deleted together, re-upload after deletion converting afresh rather than `already_converted`, deletion with the outbox file already gone, and an unwritable outbox producing a populated `kept_files` with the rows still removed
- [X] T015 [P] [US2] Extend `tests/unit/test_db.py` with the row-deletion transaction: `conversion_part` rows disappear by cascade, foreign keys are satisfied by the delete order, `batch` rows survive, and no row for the hash remains (data-model.md INV-1)

### Implementation for User Story 2

- [X] T016 [P] [US2] In `src/pdf2md/models.py`, add `content_hash` to `JobSummary`, add `document_outputs: list[OutputFile]` and `retained_upload: bool` to `JobDetail`, and add the `DeletionResult` model with `job_ids`, `filename`, `removed_files`, `kept_files`, and `upload_discarded` (data-model.md)
- [X] T017 [US2] In `src/pdf2md/db.py`, add `delete_document_rows(content_hash)` performing the one transaction in data-model.md's order — `conversion_job`, then `markdown_output`, then `source_document` — returning the deleted job ids, and comment why the order is forced by the foreign keys
- [X] T018 [US2] In `src/pdf2md/db.py`, add a `content_hash` filter to `job_views()` beside the existing `batch_id`, `statuses`, and `since`
- [X] T019 [P] [US2] In `src/pdf2md/storage.py`, add `delete_outbox_files(filenames)` unlinking with `missing_ok=True` and returning removed and kept lists, and extend the `delete_outbox_file` docstring — its "only outbox deletion the service performs" note is no longer true and should say what else does it now
- [X] T020 [US2] Create `src/pdf2md/deletion.py` with `delete_document(db, storage, job_id) -> DeletionResult`: refuse when any job of the document is in `IN_FLIGHT_STATUSES`, then unlink outbox files, then the inbox PDF and its part files, then the rows — in that order and never the reverse (research.md R7, data-model.md INV-1 to INV-5)
- [X] T021 [US2] Log every deletion from `src/pdf2md/deletion.py` using `log_job` from `pdf2md.logging_config`, naming the document, every job id removed, and each file removed or kept, so the outbox can be reconciled afterwards (FR-024)
- [X] T022 [US2] In `src/pdf2md/api/jobs.py`, add the `DELETE /{job_id}` handler delegating to `deletion.delete_document`, raising `ApiError(409, "still_converting", …)` and `ApiError(404, "already_deleted", …)` with messages safe to display verbatim
- [X] T023 [US2] In `src/pdf2md/api/jobs.py`, extend `to_summary` with `content_hash`, extend `to_detail` with `document_outputs` (every output for the hash, unfiltered by job id) and `retained_upload` from `storage.has_inbox_file`, and add the `content_hash` query parameter to `list_jobs`
- [X] T024 [US2] Add the confirmation dialog's content template to `src/pdf2md/static/index.html` and its styles to `src/pdf2md/static/styles.css`, with **Cancel** as the default-focused control (FR-015)
- [X] T025 [US2] In `src/pdf2md/static/app.js`, implement the four-step flow from `contracts/page-layout.md`: take the `content_hash` from the job object in `state.jobs` — not from a DOM dataset — then fetch `GET /api/jobs?content_hash=…` and `GET /api/jobs/{job_id}` in parallel, build the confirmation from `document_outputs` and the counted entries — never from `outputs`, never from the loaded rows (X4, X5) — then `DELETE` on confirmation only, drop every id in `job_ids`, call `refreshHealth()`, and report the outcome from the response including any `kept_files` (X6); when `kept_files` is non-empty, state that the outbox count no longer matches the folder (FR-018). Render the Delete control disabled, with the reason, for a row whose document has work in flight (FR-019), and surface a 409 message verbatim if one arrives anyway

**Checkpoint**: Deletion works end to end. Run quickstart scenarios 4–8.

---

## Phase 5: User Story 3 - Open the full detail of a single conversion (Priority: P3)

**Goal**: The text the clamp hides, and everything else the service recorded, one click away.

**Independent Test**: For a conversion with a long failure, open its detail, confirm the complete
text plus timings, size, page count, attempt, produced files, and engine errors; close it and
confirm the list and the scroll position are unchanged and focus is back on the opener.

### Tests for User Story 3

- [X] T026 [P] [US3] Extend `tests/unit/test_page_layout.py` with invariants D1–D3: a detail `<dialog>` exists in `src/pdf2md/static/index.html`, the page opens it with `showModal`, and no truncation class is applied inside it

### Implementation for User Story 3

- [X] T027 [US3] Add the detail dialog's content template to `src/pdf2md/static/index.html` — filename heading, full explanation, a fact list, and a file list
- [X] T028 [US3] Style the detail dialog's fact list and file list in `src/pdf2md/static/styles.css`, reusing the existing type scale and muted colour
- [X] T029 [US3] In `src/pdf2md/static/app.js`, add `fetchDetail(jobId)` calling the existing `GET /api/jobs/{job_id}` and `renderDetailDialog(detail)` showing the unclamped explanation, status, queue position, part counter, created/started/ended times, `processing_seconds`, size, page count, attempt, `document_outputs` with sizes and section titles, and `engine_errors` verbatim (FR-009)
- [X] T030 [US3] In `src/pdf2md/static/app.js`, wire the "More" button from T010 and a new `Details` button in the Actions cell to open the dialog, and enable the "More" button that T010 left inert
- [X] T031 [US3] In `render`/`refreshJobs` in `src/pdf2md/static/app.js`, re-render an open detail dialog on the existing 2-second tick while its job is non-terminal, so it keeps pace instead of showing the state at open time (FR-011, D2)

**Checkpoint**: All three stories work independently. Run quickstart scenario 3.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T032 [P] Add a pointer in `specs/001-docling-pdf2md-stack/contracts/web-api.md` from `GET /api/jobs` and `GET /api/jobs/{job_id}` to `specs/002-job-list-layout-delete/contracts/web-api-deletion.md`, so the additive fields are discoverable from the contract that documents those endpoints
- [X] T033 [P] Update `README.md` where it describes the page: the list is fixed-width with a detail view, and conversions can be deleted from it — including that deleting removes the Markdown, the retained upload, and every entry for that document
- [ ] T034 Run every scenario in `specs/002-job-list-layout-delete/quickstart.md` against a running page, including the narrow-window check that only a browser can answer (SC-001)
- [X] T035 Run `ruff check src tests && ruff format --check src tests` and the full `pytest` suite; confirm `pyproject.toml` gained no dependency

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Blocks US2 and US3. Does **not** block US1
- **US1 (Phase 3)**: Depends on Setup only — startable immediately, shippable alone
- **US2 (Phase 4)**: Depends on Foundational. Independent of US1, but T025 puts the Delete control in the Actions cell that T011 creates, so building US1 first avoids editing the same rendering code twice
- **US3 (Phase 5)**: Depends on Foundational. T030 enables the button T010 left inert, so US1 first
- **Polish (Phase 6)**: After the stories being shipped are done

### Within User Story 2

Server before page: T016 → T017/T018/T019 → T020 → T021 → T022/T023 → T024 → T025. The server
half is fully testable by T013–T015 before a line of the page changes.

### Parallel Opportunities

- T004 and T005 (different files, same phase)
- T013, T014, T015 — three test files, no overlap
- T016 and T019 — `models.py` and `storage.py` are independent
- T032 and T033 — documentation in different files
- With more than one person: US1 and the server half of US2 proceed at the same time

---

## Parallel Example: User Story 2

```bash
# The three test files first, together:
Task: "Contract tests for DELETE and the payload additions in tests/contract/test_deletion.py"
Task: "Integration tests for the deletion journeys in tests/integration/test_delete_flow.py"
Task: "Row-deletion transaction tests in tests/unit/test_db.py"

# Then the two independent source files:
Task: "DeletionResult and the JobSummary/JobDetail additions in src/pdf2md/models.py"
Task: "delete_outbox_files() in src/pdf2md/storage.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 → Phase 3. Phase 2 is not required for it.
2. **Stop and validate**: quickstart scenarios 1–2 at several window widths.
3. Ship. The reported defect is fixed; nothing else has moved.

### Incremental Delivery

1. Setup → US1 → ship (MVP)
2. Foundational → US2 → ship (deletion, the second half of the request)
3. US3 → ship (the detail view the clamp implies)
4. Polish

### Notes

- `[P]` means different files with no incomplete dependency between them
- Verify the new tests fail before implementing — particularly T013's `already_converted` case,
  which passes accidentally if `document_outputs` is wired to the job-scoped `outputs`
- Commit per task or per logical group
- No dependency may be added to `pyproject.toml`, and no asset may reference an external origin —
  `tests/unit/test_static_assets.py` enforces the second
