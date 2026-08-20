# Quickstart: Validating the Fixed-Width List and Conversion Deletion

**Feature**: `002-job-list-layout-delete` | **Date**: 2026-08-19 | **Phase**: 1

How to prove this feature works. The automated half runs against the stub engine and needs no
4.4 GB image; the manual half covers what only a browser can answer (research.md R12).

## Prerequisites

```bash
uv sync --extra dev          # or: pip install -e '.[dev]'
```

## Automated checks

```bash
pytest                                                   # the whole suite
pytest tests/unit/test_page_layout.py                    # layout invariants L1–L5, D1–D3, X1–X4
pytest tests/contract/test_deletion.py                   # DELETE + the payload additions
pytest tests/integration/test_delete_flow.py             # deletion end to end
ruff check src tests && ruff format --check src tests
```

Expected: all green, and no new dependency in `pyproject.toml`. The layout tests read the three
page assets and assert the invariants in
[`contracts/page-layout.md`](contracts/page-layout.md); the endpoint tests assert the shapes in
[`contracts/web-api-deletion.md`](contracts/web-api-deletion.md), including that
`GET /api/jobs/{job_id}` on an `already_converted` job returns an empty `outputs` and a populated
`document_outputs` — the distinction the confirmation depends on.

## Running the page

```bash
PDF2MD_ENGINE_API_KEY=dev \
PDF2MD_DB_PATH=.local/db/pdf2md.sqlite \
PDF2MD_INBOX_PATH=.local/inbox \
PDF2MD_OUTBOX_PATH=.local/outbox \
PDF2MD_ENGINE_URL=http://127.0.0.1:5001 \
  uvicorn pdf2md.main:app --reload --port 8080
```

Then open `http://127.0.0.1:8080`. Without a real engine behind it, uploads queue and fail — which
is enough for the layout scenarios below, since a failure message is the widest thing the page
renders. For the deletion scenarios, run the full stack (`deploy/`) or seed the outbox by hand.

---

## Scenario 1 — the list stays inside the window (SC-001, SC-002)

1. Upload a batch containing a document that fails with a long reason, one that finishes with a
   page-gap caution, and one still waiting.
2. **Expected**: no horizontal scrollbar at any window width from ~900 px to full screen. Confirm
   by dragging the window narrower; `document.documentElement.scrollWidth` never exceeds
   `clientWidth`.
3. Note the x-position of each column boundary, let the batch run to completion, and check them
   again. **Expected**: identical, and no row has moved.

## Scenario 2 — long text is previewed, not spilled (SC-001)

1. Find the row with the longest failure reason.
2. **Expected**: the explanation wraps over at most three lines, is cut off cleanly, and the cell
   shows a "More" control. The Document column shows the whole filename wrapped, not stretched.
3. Tab to the "More" control and press Enter. **Expected**: the detail dialog opens.

## Scenario 3 — the detail dialog (SC-004)

1. Open any row's Details.
2. **Expected**: the complete explanation with no truncation, plus status, timings,
   `processing_seconds`, size, page count, attempt, every produced file with its size and section
   title, and any engine errors.
3. Press Escape. **Expected**: it closes, the list is unchanged, and focus is back on the control
   that opened it.
4. Open the dialog for a converting document and wait. **Expected**: it keeps pace without being
   reopened (FR-011).

## Scenario 4 — deleting, and cancelling (SC-005, SC-007)

1. On a finished conversion, choose Delete.
2. **Expected**: a confirmation naming that document, listing each Markdown file by name, saying
   how many list entries will disappear, and stating that this cannot be undone. Cancel has focus.
3. Press Escape. **Expected**: nothing is removed — verify the outbox still holds the files.
4. Choose Delete again and confirm.
5. **Expected**: within 2 seconds the entry is gone, every listed file is gone from the outbox,
   the retained PDF is gone from the inbox, and the outbox count in the header has dropped without
   a page reload.

## Scenario 5 — split documents and siblings (FR-017, FR-021, SC-008)

1. Convert a document long enough to produce section files, and a second unrelated document.
2. Delete the split one. **Expected**: every section file goes, the other document's output is
   untouched.
3. Upload the same PDF twice so the second is recorded `already_converted`, then delete from the
   **`already_converted`** row. **Expected**: the confirmation says two entries will disappear and
   lists every Markdown file by name — not an empty file list. This is the case that catches a
   confirmation built from `outputs` instead of `document_outputs`.
4. Confirm. **Expected**: neither entry remains, and no row offers a download of a removed file.
5. With more than 100 conversions in history, delete one whose sibling is old enough to fall
   outside the default page of the list. **Expected**: the confirmation still says two entries —
   the count comes from `GET /api/jobs?content_hash=…`, not from the rows on screen.

## Scenario 6 — re-upload after deletion (SC-009)

1. Upload a PDF, let it convert, delete it, upload the same file again.
2. **Expected**: a real conversion starts. It is **not** reported `already_converted`.

## Scenario 7 — the refusals (FR-019, FR-020, FR-022, R10)

| Do this | Expect |
|---|---|
| Delete while the document is queued or converting | The Delete control is visible but disabled and gives the reason; nothing removed |
| Start a conversion of the document between rendering the row and confirming | 409 with the server's message; the list is left alone |
| Delete a document with a finished job *and* a retry in flight | Same refusal — the in-flight sibling protects it |
| Remove the `.md` from the outbox by hand, then delete | Succeeds; records cleared; no error |
| Delete the same row from two browser tabs | Both end with the row gone and no unexplained error |
| Make the outbox read-only, then delete | Records cleared, surviving files named in the outcome, and the outcome says the outbox count no longer matches the folder |

## Scenario 8 — nothing else was touched (SC-008, SC-010)

After the scenarios above, list the outbox. **Expected**: exactly the files belonging to documents
that were never deleted, plus any file the service never wrote, untouched. Then check the log:
every deletion appears, naming the document, the job ids, and each file removed or kept.
