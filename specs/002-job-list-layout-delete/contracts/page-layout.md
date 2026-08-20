# Contract: Documents List Layout and Detail Dialog

**Feature**: `002-job-list-layout-delete` | **Consumer**: the operator, through a LAN browser

The page stays what it is: `index.html`, `app.js`, `styles.css`, served from this origin, no CDN,
no web font, no framework, no external request (FR-025 of feature 001). This contract fixes the
parts of its appearance that requirements depend on, so a later change cannot quietly undo them.

---

## The table

`table.jobs` uses fixed layout. Column widths come from the header row; content never sets them.

The row's `content_hash` is carried in the page's own state, not rendered — it identifies
siblings for the deletion flow and appears nowhere on screen.

| Column | Width | Contents | Overflow behaviour |
|---|---|---|---|
| Document | 30% | Submitted filename | Wraps within the column, breaking inside a word if it has no spaces; full value available in the detail dialog |
| Status | 15% | `display_status` from the API | Wraps; **never** `white-space: nowrap` |
| Detail | 40% | The explanation currently rendered by `renderDetail` | Clamped to 3 lines, with a "More" control when clamped |
| Actions | 15% | Download, Details, Delete | Stacked, never side-scrolled |

**Invariants**

- **L1**: The table's width is bounded by its container at every viewport width; the page never
  scrolls horizontally (FR-001).
- **L2**: Column widths and row order do not change as statuses change. Only cell contents change
  (FR-004).
- **L3**: No cell in the table sets `white-space: nowrap`. Long text wraps; long unbroken tokens
  break (`overflow-wrap: anywhere`) (FR-001, FR-007).
- **L4**: Status remains distinguishable without colour — the existing `state-*` colours stay, and
  the status text itself carries the meaning (FR-005).
- **L5**: The action controls are present in every row that supports them and need no horizontal
  scrolling to reach (FR-006).

### The Detail cell

Three lines, then cut. When the text is clamped, a "More" control appears in the cell and opens
the detail dialog (FR-002, FR-012). The control is a real `<button>`: reachable by keyboard,
announced to a screen reader, and never a bare styled `<span>`.

Whether the text is clamped is decided after insertion by comparing `scrollHeight` against
`clientHeight` for that cell — CSS can hide the overflow but cannot report it.

The first sentence of every message carries the decision (FR-003). This is already true of the
strings in `renderDetail` and the failure reasons the API produces, and it is a constraint on any
new message: whatever is worth acting on goes in the first sentence.

### The Actions cell

| Control | When shown | Behaviour |
|---|---|---|
| Download | `download_url` is present | Anchor with the `download` attribute. Label is the word "Download", not the output filename — a section filename is long enough to have been a width problem of its own. The filename is shown in the dialog and in the control's accessible title |
| Details | Always | Opens the detail dialog for that job |
| Delete | Always | Starts the deletion flow below |

---

## The detail dialog

A native `<dialog>` opened with `showModal()`, one element reused for whichever row is inspected.
Escape closes it, focus is trapped while it is open, and focus returns to the control that opened
it — all from the element itself (FR-010).

**Contents** (from `GET /api/jobs/{job_id}`, FR-009):

- The filename and the full, unclamped explanation — no truncation anywhere in the dialog.
- Status, queue position where relevant, and the part counter for a split document.
- Created, started, and ended times, and `processing_seconds`.
- Size, page count, and attempt number.
- Every produced file with its size and section title, and its download link.
- `engine_errors`, verbatim, when there are any.

**Invariants**

- **D1**: Opening or closing the dialog does not reflow the list behind it (FR-004).
- **D2**: While the inspected job is non-terminal, the dialog re-renders on the existing 2-second
  poll rather than showing the state at open time (FR-011).
- **D3**: The dialog can be dismissed by keyboard and by pointer, and focus returns to its opener
  (FR-010).

---

## The deletion flow

Four steps, no shortcuts. For a row whose document has a conversion at the engine (`submitted` or
`running`, never merely `queued`) — judged from the statuses of the rows sharing its
`content_hash` — the Delete control is **rendered and disabled**,
with the reason in its `title` and in its accessible description: the operator can see that
deletion exists and why it is not available yet (FR-019). The server refuses such a deletion
regardless (FR-022).

1. **Gather the facts.** Two calls, in parallel, on the click:
   `GET /api/jobs?content_hash=<hash>` for the entries that will disappear, counted by the page,
   and `GET /api/jobs/{job_id}` for `document_outputs` and `retained_upload`.
   The `content_hash` comes from the row itself, which now carries it.
2. **Confirm.** A modal `<dialog>` naming the document and stating what will be removed: the
   number of list entries, each Markdown file by name from `document_outputs`, and the retained
   upload when `retained_upload` is true. It states that the removal cannot be undone. Its default
   focus and its default action are **Cancel** (FR-015). Escape, the backdrop, and Cancel all mean
   no.
   If either call fails, the confirmation is not shown and the operator is told why. A
   confirmation that cannot describe what it will destroy is not offered.
3. **Delete.** `DELETE /api/jobs/{job_id}` on confirmation only. On success, every id in
   `job_ids` is removed from the list, the health line is refreshed so the outbox count is right
   (FR-025), and the outcome is reported. When `kept_files` is non-empty, the report names the
   files that survived (FR-018). A 404 is reported as a completed deletion; a 409 shows the
   server's message and leaves the list alone.
4. **Reconcile.** `job_ids` is authoritative. If it names entries the confirmation did not
   predict, those rows are dropped too and the outcome reports the number actually removed.

**Invariants**

- **X1**: No `DELETE` is ever issued without a confirmation the operator accepted (FR-014, SC-007).
- **X2**: Dismissing the confirmation by any means changes nothing on the server and nothing on
  the page (FR-015).
- **X3**: The confirmation names one document. There is no select-all, no multi-select, and no
  bulk control anywhere on the page (FR-026).
- **X4**: The confirmation's file list comes from `document_outputs`, never from `outputs`.
  For an `already_converted` row the two differ, and `outputs` is empty — a confirmation built
  from it would promise to remove nothing while removing every section file of the document.
- **X5**: The entry count comes from `GET /api/jobs?content_hash=…`, not from the rows currently
  loaded, which are capped by the poll's `limit` and can omit an older sibling. The confirmation
  may never state fewer entries than the deletion removes; an operator decision, not an
  optimisation to be traded away for one fewer request.
- **X6**: Every deletion outcome is reported from the `DELETE` response, not assumed from the
  request. What the page claims was removed is what the server says was removed.

---

## Accessibility floor

- Every interactive element is a `<button>` or an `<a>`, reachable and operable by keyboard.
- The dialogs are `<dialog>` elements, labelled by their heading.
- The list keeps its table semantics: real `<th scope="col">` headers, one row per conversion.
- Status is never conveyed by colour alone (L4).
- Deletion outcomes are announced in a region with `aria-live="polite"`, as rejections already are.
