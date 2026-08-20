# Contract: Deletion Endpoint and the Payloads Around It

**Feature**: `002-job-list-layout-delete` | **Consumer**: the operations page (and any LAN client)

Extends [`specs/001-docling-pdf2md-stack/contracts/web-api.md`](../../001-docling-pdf2md-stack/contracts/web-api.md).
Its conventions hold unchanged: ISO-8601 UTC timestamps, the single
`{ "error": { "code": …, "message": … } }` error shape with a message safe to display verbatim,
and no authentication — presence on the local network is the authorization (FR-024 of that
feature). No endpoint below requires a header a browser does not send by default.

One new route. The rest of this contract is three additive fields and one query filter on
endpoints that already exist, which together give the confirmation dialog its facts
(research.md R5).

---

## Changes to existing endpoints

### `GET /api/jobs` — new `content_hash` filter, new `content_hash` field

| Query param | Default | Notes |
|---|---|---|
| `content_hash` | — | Return only conversions of this document |

Beside the existing `limit`, `batch_id`, `status`, and `since`. The page calls
`GET /api/jobs?content_hash=…` once, when the operator chooses Delete, and counts the rows to
state exactly how many entries a deletion will remove (FR-021). Counting the rows already on the
page would undercount a sibling older than the `limit` window.

Every object in `jobs` gains one field:

```json
{ "job_id": "3ab…", "filename": "annual-report.pdf", "content_hash": "4f2a91b0c7d3…", "…": "…" }
```

Two rows sharing a `content_hash` are two conversions of the same PDF. They share its Markdown and
its retained upload, and they are deleted together.

**Unknown hash**: 200 with an empty `jobs` array, as with any filter that matches nothing.

### `GET /api/jobs/{job_id}` — two new fields

```json
{
  "job_id": "3ab…",
  "filename": "annual-report.pdf",
  "content_hash": "4f2a91b0c7d3…",
  "outputs": [ { "filename": "annual-report--4f2a91b0c7d3.md", "bytes": 741960, "section_title": null } ],
  "document_outputs": [ { "filename": "annual-report--4f2a91b0c7d3.md", "bytes": 741960, "section_title": null } ],
  "retained_upload": true
}
```

| Field | Notes |
|---|---|
| `document_outputs` | Every file recorded for this document, whichever conversion wrote it — including each section file of a split document (FR-017). This is what a deletion removes, and what the confirmation must list |
| `retained_upload` | Whether the uploaded PDF is still on the server. False once the retention reaper has taken it |

`outputs` is unchanged and still means *the files this job wrote*. The two differ for an
`already_converted` job, whose `markdown_output` rows carry the original job's id: its `outputs`
is empty, its `document_outputs` is not. **A confirmation built from `outputs` would tell the
operator that nothing will be removed while removing every section file of the document.**

---

## `DELETE /api/jobs`

Removes **every** document: every entry, every Markdown file, every retained upload (FR-027).
Irreversible, and it takes successful conversions with it — the Markdown in the outbox belongs
to the documents being deleted.

No request body. The confirmation is the page's responsibility, and it must say that successful
conversions go too.

**200**

```json
{
  "documents_deleted": 2,
  "job_ids": ["3ab…", "9de…"],
  "removed_files": ["one--4f2a.md", "two--91b0.md"],
  "kept_files": [],
  "skipped": [
    { "filename": "in-progress.pdf", "reason": "being converted right now — nothing of it was removed" }
  ]
}
```

A document the engine is converting is **skipped**, not refused: one busy conversion must not
prevent clearing everything else, and must not be destroyed either. Everything else follows the
single-document rules — files before rows, only recorded outputs, a missing file is not a failure.

An empty list returns `documents_deleted: 0` and changes nothing.

---

## `DELETE /api/jobs/{job_id}`

Deletes the source document this conversion belongs to: every conversion of it, every Markdown
file it produced, and the retained upload (FR-016). Irreversible.

No request body, no query parameters. Confirmation is the page's responsibility (FR-014); the
service does not accept a `?confirm=` flag, because a flag a client can set is not a confirmation
a person gave.

**200** — deleted

```json
{
  "job_ids": ["3ab…", "9de…"],
  "filename": "annual-report.pdf",
  "removed_files": [
    "annual-report--4f2a91b0c7d3--01-introduction.md",
    "annual-report--4f2a91b0c7d3--02-methodology.md"
  ],
  "kept_files": [],
  "upload_discarded": true
}
```

`job_ids` lists every entry the page must drop, not only the one addressed. It is also the page's
reconciliation: if it names more entries than the confirmation predicted, the list still ends up
correct.

**200** — deleted, but something could not be removed

`kept_files` is non-empty when a file could not be unlinked (an unwritable outbox, most likely).
The database rows are gone regardless, so the service's records match what it can still see. The
page reports the partial outcome and names the surviving files (FR-018).

**409** `still_converting` — a conversion of this document is submitted or running, meaning the
engine holds it. Checked across every job of the document, not just the one named, so a retry
running out of view cannot write its output into an outbox the operator believes they emptied
(FR-022).

**`queued` does not refuse.** A waiting job has no engine task, so deleting its row is what takes
it out of the queue. This matters most when the dispatcher has stopped claiming work: the entry
stays removable instead of becoming permanent.

```json
{
  "error": {
    "code": "still_converting",
    "message": "\"annual-report.pdf\" is being converted right now. Wait for it to finish, then delete it."
  }
}
```

The page also disables the Delete control for such a row, with the reason shown, using the
statuses it can see. That copy of the rule is a courtesy; this response is the rule. When they disagree — a conversion that started
between render and click — the operator sees this message.

**404** `already_deleted` — the job is gone. Returned when a second browser tab deleted it first,
or when its history was pruned between render and click. The page treats any 404 from a delete as
a completed deletion and drops the row, so the operator sees the outcome they asked for.

---

## Behavioural guarantees the page relies on

1. **Nothing else is touched.** A deletion unlinks only the paths recorded in `markdown_output`
   for that document and the two inbox names derived from its content hash. It never scans a
   directory and never takes a path from the request (FR-017, SC-008).
2. **A missing file is not a failure.** Deleting a conversion whose Markdown was already removed
   by hand succeeds and clears the remaining records (FR-020).
3. **Re-uploading converts again.** After a deletion, the same PDF is a new document: no
   `source_document` row survives to make `claim_already_converted` short-circuit it (FR-023).
4. **Files go before rows.** An interrupted deletion leaves records pointing at absent files —
   which every read path already handles — never files with no record (research.md R7).
5. **`GET /api/health` is immediately correct.** `outbox.documents` counts `markdown_output`
   rows, so it reflects the deletion the moment it commits; the page refreshes health after a
   delete rather than waiting for its timer (FR-025).
6. **Every deletion is logged**, naming the document, the job ids, and each file removed or kept,
   so the outbox can be reconciled afterwards (FR-024).
7. **`content_hash` is an opaque identifier to the page.** It is used to group and to filter,
   never parsed, never shown, and never used to build a path.
