# Implementation Plan: Fixed-Width Document List with Detail on Demand and Conversion Deletion

**Branch**: `002-job-list-layout-delete` | **Date**: 2026-08-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-job-list-layout-delete/spec.md`

## Summary

Two changes to the operations page, and one new server capability behind the second.

The documents list becomes a fixed-layout table: column widths come from the header row, the
explanation cell clamps to three lines with a "More" control, and a native `<dialog>` carries the
full detail — fed by `GET /api/jobs/{job_id}`, which has existed since feature 001 and which
nothing has ever called. That half touches only `styles.css`, `app.js`, and `index.html`.

Deletion is new, and it is one route: `DELETE /api/jobs/{job_id}` removes the *source document*
the conversion belongs to — every conversion of it, every Markdown file it produced, its retained
upload, and the record that makes a re-upload count as already converted. The confirmation gets
its facts from payloads that already exist, extended by three fields and one query filter:
`content_hash` on `JobSummary` so the page can identify a document's other conversions,
`document_outputs` and `retained_upload` on `JobDetail` so it can name every file that will go.
The logic lives in a new `deletion.py`; the schema does not change.

## Technical Context

**Language/Version**: Python 3.12 (server); ES2022, no build step (page)

**Primary Dependencies**: FastAPI, Pydantic v2, `pydantic-settings`, httpx, pypdf — **no new
dependency, server or client**. The page remains three self-hosted assets with no framework.

**Storage**: SQLite (WAL, `foreign_keys=ON`) for the registry; the inbox and outbox bind mounts
for files. No migration: this feature only removes rows.

**Testing**: pytest with the repository's `unit` / `contract` / `integration` markers, against the
stub engine in `tests/stubs/docling_stub.py`; ruff for lint and format.

**Target Platform**: A container on a Mac mini, reached from the LAN only. The image has no
route to the internet and neither does CI.

**Project Type**: Web service with a server-served static page — one FastAPI app, one page.

**Performance Goals**: A deletion of a document with up to 50 section files completes in under
2 s (SC-005). The list stays responsive at 500 rows on a 2-second poll; fixed table layout makes
that cheaper than it is today.

**Constraints**: No egress from the page assets (no CDN, no font, no analytics). Every response
keeps the single `{"error": {...}}` shape with a message safe to show verbatim. Filesystem writes
stay atomic. One SQLite writer. Ruff line length 100.

**Scale/Scope**: Hundreds of jobs in history, tens of section files per document, one operator at
a time in practice — but two browser tabs are assumed and handled.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is the **unfilled template** — every principle is still
`[PRINCIPLE_N_NAME]`. There are no ratified gates to check against, and this plan does not invent
any. Recorded plainly rather than reported as a pass.

In its place, the plan is gated on the invariants this repository already enforces in code and
tests, which are the closest thing to a constitution it has:

| De-facto gate | Enforced by | This feature |
|---|---|---|
| The page reaches nothing but this origin | `tests/unit/test_static_assets.py` | **Pass** — `<dialog>`, `line-clamp`, and `fetch` are platform features; nothing added |
| One error shape, message safe to display | `main.py` handlers, `contracts/web-api.md` | **Pass** — `ApiError` for both refusals |
| Contract-first: endpoints documented before built | `specs/*/contracts/` | **Pass** — [`contracts/web-api-deletion.md`](contracts/web-api-deletion.md) |
| Tests beside the code, three markers | `pyproject.toml`, `tests/` | **Pass** — unit, contract, and integration tests planned |
| Destructive filesystem work is narrow and deliberate | `storage.delete_outbox_file` docstring | **Pass** — INV-2: only paths recorded in `markdown_output`, never a scan |
| No dependency the offline image cannot carry | `Dockerfile`, CI | **Pass** — none added; browser automation rejected for this reason (research.md R12) |

**Post-Phase-1 re-check**: unchanged. The design adds one module, two endpoints, two transport
models, and no dependency. The one place it widens an existing behaviour — `storage`'s deletions,
previously restricted to a document replacing its own section files — is bounded by INV-2 and
covered by SC-008.

## Project Structure

### Documentation (this feature)

```text
specs/002-job-list-layout-delete/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — R1–R13
├── data-model.md        # Phase 1 output — deletion order, invariants, transport models
├── quickstart.md        # Phase 1 output — 8 validation scenarios
├── contracts/
│   ├── web-api-deletion.md   # DELETE /api/jobs/{job_id} + the payload additions
│   └── page-layout.md        # Table, dialog, and deletion-flow invariants
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 — created by /speckit-tasks, not here
```

### Source Code (repository root)

```text
src/pdf2md/
├── deletion.py            # NEW — delete_document(); the ordering rule and the refusals
├── db.py                  # + delete_document_rows(), content_hash filter on job_views()
├── storage.py             # + delete_outbox_files(), reuse delete_inbox_file/delete_part_files
├── models.py              # + DeletionResult; JobSummary.content_hash;
│                          #   JobDetail.document_outputs, JobDetail.retained_upload
├── api/
│   └── jobs.py            # + DELETE /{job_id} — thin handler; two shaping changes
└── static/
    ├── index.html         # + <dialog> for detail, + <dialog> for confirmation
    ├── styles.css         # table-layout: fixed, column widths, 3-line clamp, dialog styles
    └── app.js             # + detail dialog, + deletion flow, + clamp detection

tests/
├── unit/
│   ├── test_page_layout.py    # NEW — asset invariants L1–L5, D1–D3, X1–X4
│   └── test_db.py             # + row-deletion ordering and cascade
├── contract/
│   └── test_deletion.py       # NEW — both endpoints against web-api-deletion.md
└── integration/
    └── test_delete_flow.py    # NEW — split output, siblings, re-upload, refusals, partial failure
```

**Structure Decision**: The existing single-project layout is kept exactly. Deletion gets its own
module because it spans the database, both storage locations, and the log, and carries rules of
its own — which is how `naming.py`, `sectioning.py`, and `pdfinfo.py` are already organised, with
`api/` reduced to shaping requests and responses (research.md R13).

## Implementation Phases

Ordered so each phase is independently valuable and independently testable, matching the spec's
story priorities.

**Phase A — the fixed layout (US1, P1).** `styles.css` and `app.js` only. `table-layout: fixed`,
a width on every header, `overflow-wrap: anywhere`, the three-line clamp, removal of
`white-space: nowrap` from the status cell, and the Actions column replacing the bare Markdown
link. Ships alone and fixes the reported defect. Verified by `test_page_layout.py` and quickstart
scenarios 1–2.

**Phase B — deletion (US2, P2).** `models.py`, `db.py`, `storage.py`, `deletion.py`, the two
handlers, then the page's confirmation flow. The server side is testable before any page change.
Verified by `test_deletion.py`, `test_delete_flow.py`, and quickstart scenarios 4–8.

**Phase C — the detail dialog (US3, P3).** `index.html`, `app.js`, `styles.css`, fed by the
existing detail endpoint. Completes the clamp by giving the hidden text somewhere to live.
Verified by `test_page_layout.py` and quickstart scenario 3.

Phase B does not depend on Phase A, but the Actions cell from Phase A is where the Delete control
goes, so building A first avoids touching the same rendering code twice.

## Risks and the calls made about them

| Risk | Call |
|---|---|
| A deletion interrupted halfway leaves inconsistent state | Files before rows, so the residue is records pointing at absent files — a state every read path already handles (research.md R7, data-model.md INV-5) |
| Deleting one conversion breaks its `already_converted` sibling | The unit of deletion is the document; siblings go together and the confirmation says so before the operator commits (FR-021, R6) |
| A retry running out of view writes into an emptied outbox | Refuse while *any* job of the document is in flight, not just the one named (FR-022, R9) |
| The layout requirement is the one this repo cannot test in a browser | Static assertions on the assets for the regressions that actually occur, plus scripted manual checks; Playwright rejected against the offline posture (research.md R12) |
| A confirmation that describes something other than what gets deleted | Its file list comes from `document_outputs`, never the job-scoped `outputs`, and its entry count from a `content_hash` query, never the loaded rows (contracts/page-layout.md X4, X5) |
| The page and the server disagree about whether a delete is allowed | The page disables the control with a reason as a courtesy; the 409 is the rule, and its message is what the operator sees (contracts/web-api-deletion.md) |

## Complexity Tracking

No constitution gates exist to violate, and the design adds no structure that needs defending:
one module, one route, one response model, three additive fields, one query filter, zero
dependencies, zero schema change.

An earlier draft added a second route, `GET /api/jobs/{job_id}/deletion`, to describe a deletion
before performing it. It was removed on the operator's direction (research.md R5): extending
payloads that already exist is the smaller change, and the facts the confirmation needs are facts
those payloads could reasonably have carried all along.

Two consequences are worth naming, because they are where the complexity moved rather than
disappeared:

- **The in-flight rule is now stated twice** — once in the page, which disables the control, and once
  in the server, which returns 409. The server's copy is authoritative and the page's is a
  courtesy, but they can drift, and a test should hold them together.
- **The confirmation is assembled from two responses rather than read from one.** The failure mode
  to guard is a confirmation built from `JobDetail.outputs` instead of `document_outputs`: for an
  `already_converted` row `outputs` is empty, so the dialog would promise to remove nothing while
  the deletion removed every section file. That is contract invariant X4 and it needs a test of
  its own.
