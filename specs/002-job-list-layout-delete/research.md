# Research: Fixed-Width Document List and Conversion Deletion

**Feature**: `002-job-list-layout-delete` | **Date**: 2026-08-19 | **Phase**: 0

Every unknown in the plan's Technical Context is resolved below. Nothing here adds a runtime
dependency: the page is still three self-hosted assets and the service still ships the same
`pyproject.toml` dependency list.

---

## R1. Stopping a cell from widening its column

**Decision**: `table-layout: fixed` on `table.jobs`, an explicit width on every `<th>`, and
`overflow-wrap: anywhere` on the text cells.

**Rationale**: The table today is `border-collapse: collapse` with no `table-layout`, so the
browser uses automatic layout and sizes each column to its widest content. One 240-character
failure reason therefore sets the width of the whole table, and `.state { white-space: nowrap }`
on the status cell prevents even the status column from giving way. Fixed layout inverts the
relationship — column widths come from the header row and content wraps into them — which is
exactly FR-001 and FR-004. It also makes rendering cheaper for a several-hundred-row list,
because the browser stops measuring every cell.

**Alternatives considered**:
- `max-width` on each `<td>` with automatic layout: works, but the widths still interact with
  content and the column boundaries drift between renders, which FR-004 forbids.
- Replacing the table with a CSS grid: more control, but loses table semantics for screen
  readers and rewrites markup this feature has no other reason to touch.

---

## R2. Truncating a message to a fixed number of lines

**Decision**: A three-line clamp using `display: -webkit-box; -webkit-box-orient: vertical;
-webkit-line-clamp: 3; overflow: hidden`, paired with the standard `line-clamp: 3`, and a
"More" control in the cell for anything clamped.

**Rationale**: The `-webkit-` form is the only one with universal support today and is
implemented by every engine including Firefox; the unprefixed `line-clamp` is emitted next to
it so the rule survives the prefix being dropped. Three lines fits the longest first sentence of
every message the service produces — the failure reasons in `api/jobs.py` and the cautions in
`app.js` all lead with the actionable sentence, which is what FR-003 requires — while keeping a
50-row list scannable.

A clamp hides text without telling anyone, so FR-002 needs a visible marker. The page cannot ask
CSS whether a given cell overflowed, so the "More" control is rendered from a length comparison
in JavaScript (`scrollHeight > clientHeight` after insertion), measured once per render.

**Alternatives considered**:
- `text-overflow: ellipsis` with `white-space: nowrap`: single-line only, and the user asked for
  a multiline preview.
- A fade-out gradient instead of a control: pretty, but not operable by keyboard and invisible
  to a screen reader.

---

## R3. Where the detailed view lives

**Decision**: The native `<dialog>` element opened with `showModal()`, one instance reused for
whichever row is being inspected.

**Rationale**: `showModal()` gives FR-010 for free — Escape closes it, focus is trapped while
open, and focus returns to the element that opened it when it closes — with no focus-management
code to get wrong and no library. It is supported by every browser the operator could be using
on the LAN, and it degrades to an inert element rather than a broken page on anything older.
Being an overlay, it also cannot reflow the list behind it, which keeps FR-004 intact; an
expanding row would change row heights on every open and close.

**Alternatives considered**:
- `<details>`/`<summary>` expanding inside the row: no extra markup, but it shifts every row
  below it and cannot present the two-column fact table cleanly inside a `<td>`.
- A side panel: more room, but it either overlays the list anyway or squeezes it, and squeezing
  the list is the defect being fixed.

---

## R4. Feeding the detailed view

**Decision**: Reuse `GET /api/jobs/{job_id}`. No new read endpoint. While the inspected job is
non-terminal, the existing 2-second list poll re-fetches the detail and re-renders the dialog.

**Rationale**: `JobDetail` already carries almost everything FR-009 asks for — `engine_errors`,
`processing_seconds`, `output_bytes`, `content_hash`, and the `outputs` list of produced files —
and nothing on the page has ever called it. The endpoint exists, is documented in
`specs/001-docling-pdf2md-stack/contracts/web-api.md`, and has contract tests. Reusing it makes
FR-011 a matter of calling it again on the tick that already exists.

It gains two fields for the confirmation's sake (R5): `document_outputs`, every file recorded for
the document rather than only the files this job wrote, and `retained_upload`, whether the
uploaded PDF is still on the server. `outputs` keeps its existing job-scoped meaning so the
feature-001 contract and its tests stand.

**Alternatives considered**:
- Rendering the dialog from the summary already in `state.jobs`: no round trip, but the summary
  lacks the engine errors and the file list, which are the reason to open a detail view at all.

---

## R5. Where the confirmation gets its facts

**Decision**: No preview endpoint. `DELETE /api/jobs/{job_id}` is the only new route. The page
builds its confirmation from the content hash instead: `JobSummary` gains `content_hash`, the
page identifies a job's siblings by it, and `JobDetail` gains the two fields the confirmation
needs that the summary cannot carry.

*(Reversed from the first draft of this plan, which proposed `GET /api/jobs/{job_id}/deletion`.
Directed by the operator, 2026-08-20: extend the payloads that already exist rather than add a
route. The requirement the preview was there to satisfy still has to be met, so the additions
below are what it costs.)*

**Rationale**: FR-014 requires the confirmation to state *exactly* what will be removed, and
FR-021 requires it to say how many entries will disappear. Three facts are needed, and each has a
home in an existing payload:

| Fact | Source | Addition |
|---|---|---|
| Which entries go | `GET /api/jobs?content_hash=…`, counted by the page | `content_hash` on `JobSummary`; one query filter alongside the existing `batch_id`, `status`, and `since` |
| Which files go | `GET /api/jobs/{job_id}` | `document_outputs` on `JobDetail` — every file for the hash, not only this job's |
| Whether an upload is held | `GET /api/jobs/{job_id}` | `retained_upload` on `JobDetail` |

The sibling count is taken from a filtered query rather than from the rows already on the page.
Counting the loaded rows needs no server change at all, but the list is capped at `limit` (100 by
default, 500 at most) and a sibling older than the window would go uncounted — the confirmation
would say "1 entry" while two disappeared. Under-reporting what a destructive action will destroy
is the one failure mode this design has to exclude, and one query parameter excludes it.

The in-flight refusal (FR-019, FR-022) now lives in two places rather than one: the page hides the
control for a row whose document has work in flight, and the server refuses with 409 regardless.
That is the ordinary optimistic-UI arrangement — the page's copy is a courtesy, the server's is the
rule — and the 409 message is what the operator sees if the two ever disagree.

**Alternatives considered**:
- The preview endpoint, `GET /api/jobs/{job_id}/deletion`: puts all three facts and the in-flight
  verdict in one authoritative response, at the cost of a route that exists only to describe
  another route. Rejected on the operator's direction.
- Counting only the siblings already loaded, with no `content_hash` filter: zero server change,
  but inexact exactly when it matters (see above). Put to the operator and **rejected**
  (2026-08-20): no undercount is acceptable in a destructive confirmation. This is settled, not a
  default — a later change that drops the filter to save a request reopens it.
- `DELETE` with a `?confirm=true` guard: a flag a client sets is not a confirmation a person
  gave, and it makes the confirmation text generic, which fails FR-014's "exactly".

---

## R6. What identifies the thing being deleted

**Decision**: The route is keyed by job id for the page's convenience, but the unit of deletion
is the source document: `DELETE /api/jobs/{job_id}` removes the document that job belongs to,
every conversion of it, its outputs, and its retained upload.

**Rationale**: The clarified scope (spec Q1, FR-016) removes the `source_document` row and the
`markdown_output` rows, both of which are keyed by `content_hash` and shared by every conversion
of the same PDF. Deleting a single `conversion_job` row while leaving its siblings would leave
those siblings — typically an `already_converted` entry — pointing at files that no longer
exist, which FR-021 forbids.

The row the operator clicked is a job, so a job id is what the page has in hand at that moment;
keying the route by it keeps the call site obvious. The hash is now in the summary (R5), so this
is a matter of which identifier reads better at the point of use rather than which one the page
possesses.

**Alternatives considered**:
- `DELETE /api/documents/{content_hash}`: more honest about the unit, and it is what the handler
  does internally. Now that `JobSummary` carries the hash it is a live option; rejected only
  because a URL the operator could see in a network log would carry a 64-character hash where a
  job id says the same thing, and because every other job route is already keyed by job id.

---

## R7. The order of a deletion, and what a crash in the middle leaves behind

**Decision**: Outbox files first, then the inbox PDF and any part files, then the database rows
in a single transaction. Never the reverse.

**Rationale**: A crash between the two halves has to leave the system in the recoverable state.
Files-first leaves database rows whose files are gone, and the service already handles that
everywhere it matters: the download returns `output_removed` (404), and `claim_already_converted`
requires the file to be present before it will short-circuit a re-upload, so the document simply
converts again. Rows-first would leave `.md` files in the outbox that no record mentions —
invisible to the page, undeletable through it, and exactly the manual-cleanup situation SC-010
says the feature exists to end.

**Alternatives considered**:
- A two-phase delete with a tombstone row: survivable through any crash, but it introduces a
  state the schema does not have and that nothing else in the service would ever read.

---

## R8. The database side of a deletion

**Decision**: One transaction: `conversion_job` rows for the hash, then `markdown_output` rows,
then the `source_document` row. `conversion_part` rows disappear on their own.

**Rationale**: The order is forced by the foreign keys, which are enforced (`foreign_keys=ON` on
every connection). `conversion_job.output_filename` references `markdown_output`, so the jobs go
first; `markdown_output.content_hash` references `source_document`, so the outputs go before the
document. `conversion_part.job_id` is `ON DELETE CASCADE`, so parts need no statement of their
own. `batch` rows are left alone: `conversion_job.batch_id` is `ON DELETE SET NULL`, a batch that
loses all its jobs is harmless, and deleting it would take other documents' jobs with it.

**Alternatives considered**:
- Deferring foreign keys for the transaction: removes the ordering constraint and the reason
  anyone reading the function would understand the ordering.

---

## R9. Refusing to delete work that is still moving

**Decision**: Refuse with 409 when *any* job of the document is in `IN_FLIGHT_STATUSES`
(`queued`, `submitted`, `running`), not merely the job named in the request.

**Rationale**: FR-022. A document can have a finished job and a retry in flight at the same time;
deleting on the strength of the finished one lets the dispatcher write the retry's Markdown into
an outbox the operator believes they emptied. `IN_FLIGHT_STATUSES` is the set the inbox reaper
already uses for the same reason (`storage.reap_inbox`), so the rule stays stated once.

The check and the delete are safe against the dispatcher without a lock: the dispatcher runs as
an asyncio task in the same event loop as the request handler, the SQLite calls are synchronous,
and the handler does not await between reading the statuses and committing the deletion.

**Alternatives considered**:
- Cancelling the in-flight work and then deleting: cancellation does not exist in this service
  and inventing it here would be a second feature.

---

## R10. Two tabs deleting the same document

**Decision**: The second `DELETE` returns 404 with code `already_deleted` and a plain message;
the page treats any 404 from a delete as success and drops the row.

**Rationale**: The spec's edge case asks for "success or a plain 'already removed', never an
unexplained error". The row is genuinely gone, so 404 is the honest status, and the page's
handling makes the outcome indistinguishable from a first delete for the person looking at it.
This also covers deleting a row whose history was pruned between render and click.

**Alternatives considered**:
- 204 for a second delete: fully idempotent and defensible, but the service cannot distinguish
  "already deleted" from "never existed" once the rows are gone, and reporting success for a job
  id that was never real would hide a genuine page bug.

---

## R11. Keeping the outbox count honest after a deletion

**Decision**: The page calls the health refresh immediately after a successful deletion rather
than waiting for its 15-second timer.

**Rationale**: FR-025. `outbox.documents` in `GET /api/health` is `COUNT(*)` over
`markdown_output`, so it is correct the moment the transaction commits; the only lag is the
page's own polling interval. Calling the existing refresh function costs one line.

---

## R12. How the layout requirements get verified

**Decision**: Static assertions over the three page assets in `tests/unit/test_page_layout.py`
(fixed table layout declared, every column given a width, a line clamp present, no
`white-space: nowrap` on a text cell, a `<dialog>` in the markup), plus scripted manual checks in
`quickstart.md` for what only a browser can answer.

**Rationale**: The repository has no browser automation and no JavaScript test runner, and the
existing page tests (`tests/unit/test_static_assets.py`) already work by reading the assets and
asserting invariants about them. Adding Playwright to prove "no horizontal scrollbar" would add a
browser download to an image and a CI pipeline whose defining property is that they reach nothing
on the internet, for one assertion. The static assertions catch the regressions that actually
happen here — someone reintroducing `nowrap`, or adding a fifth column without a width — and the
quickstart covers SC-001 through SC-004 by hand.

**Alternatives considered**:
- Playwright or Selenium in CI: real verification of SC-001, at the cost of the offline posture
  and roughly 400 MB of browser per run. Rejected as disproportionate.
- No verification of the layout at all: leaves the feature's central requirement untested.

---

## R13. Where the deletion logic goes

**Decision**: A new `src/pdf2md/deletion.py` holding the deletion itself, called from a thin
handler in `api/jobs.py`.

**Rationale**: The operation spans the database, both storage locations, and the log, and it has
rules of its own (the in-flight refusal, the ordering in R7, the partial-failure report of
FR-018). That is more than a route handler should hold, and the repository already keeps this
kind of logic in small focused modules — `naming.py`, `sectioning.py`, `pdfinfo.py` — with the
API layer reduced to shaping requests and responses.

**Alternatives considered**:
- Methods on `Database` and `Storage` called directly from the handler: spreads the ordering
  rule across three files and leaves no single place to test the whole operation.
