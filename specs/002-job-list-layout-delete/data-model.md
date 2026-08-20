# Data Model: Fixed-Width Document List and Conversion Deletion

**Feature**: `002-job-list-layout-delete` | **Date**: 2026-08-19 | **Phase**: 1

**No schema migration.** This feature adds no table, no column, and no index. It adds one
destructive operation over the tables defined in
[`specs/001-docling-pdf2md-stack/data-model.md`](../001-docling-pdf2md-stack/data-model.md), and
two transport models that never reach the database.

---

## Entities touched by a deletion

A deletion is keyed by a job id but operates on the **source document** that job belongs to.
Everything below is reached from one `content_hash`.

| Entity | Table | What a deletion does | Why |
|---|---|---|---|
| Conversion | `conversion_job` | Every row with this `content_hash` is deleted | FR-021: siblings share the output, so one cannot outlive the other |
| Conversion part | `conversion_part` | Deleted by cascade from `conversion_job` | Already `ON DELETE CASCADE`; no statement needed |
| Markdown output | `markdown_output` | Every row with this `content_hash` is deleted | FR-016: the record of the produced files goes with the files |
| Source document | `source_document` | The single row is deleted | FR-016, FR-023: removing it is what makes a re-upload convert again |
| Batch | `batch` | Untouched | `conversion_job.batch_id` is `ON DELETE SET NULL`; a batch that loses jobs is harmless, and deleting one would strike other documents |
| Outbox file | filesystem | Every `markdown_output.output_filename` for the hash is unlinked | FR-017, including every section file of a split document |
| Inbox file | filesystem | `{content_hash}.pdf` and `{content_hash}--part*.pdf` are unlinked | FR-016: the retained upload is discarded ahead of the retention clock |

### Ordering (enforced, not incidental)

Files first, then rows; within the transaction, children before parents (research.md R7, R8):

```text
1. outbox:   unlink every output file recorded for the hash
2. inbox:    unlink {hash}.pdf and {hash}--part*.pdf
3. one transaction:
     DELETE FROM conversion_job    WHERE content_hash = ?   -- cascades conversion_part
     DELETE FROM markdown_output   WHERE content_hash = ?
     DELETE FROM source_document   WHERE content_hash = ?
```

The database order is forced by the foreign keys, which are enforced on every connection:
`conversion_job.output_filename → markdown_output.output_filename`, and
`markdown_output.content_hash → source_document.content_hash`.

The file-before-row order is a durability choice. A crash after step 1 leaves rows whose files
are gone, which every existing read path already survives — the download answers `output_removed`,
and `claim_already_converted` refuses to short-circuit a re-upload when the file is absent. The
reverse order would leave orphan `.md` files in the outbox that no record mentions and no page
can remove.

---

## Invariants

- **INV-1**: After a successful deletion, no `conversion_job`, `markdown_output`, or
  `source_document` row mentions the `content_hash`, and no file named by those rows remains in
  either location. (FR-016, SC-006)
- **INV-2**: A deletion unlinks only paths derived from `markdown_output` rows of that hash and
  the two inbox name patterns for that hash. No directory scan, no glob over the outbox, no path
  taken from a request. (FR-017, SC-008)
- **INV-3**: A document with any job in `IN_FLIGHT_STATUSES` (`queued`, `submitted`, `running`)
  is never deleted, whichever of its jobs the request named. (FR-019, FR-022)
- **INV-4**: A missing file is not an error. `unlink(missing_ok=True)` throughout. (FR-020)
- **INV-5**: The deletion is all-or-nothing in the database and best-effort on the filesystem.
  If a file cannot be unlinked, the rows are still removed, the response names the files left
  behind, and the log records them. Leaving the rows in place instead would leave a row for a
  file the operator can no longer see the service delete. (FR-018)

---

## State transitions

None. Deletion is not a job state — there is no `deleted` status, and a deleted conversion has no
successor state. The job simply ceases to exist. This is the consequence of clarification Q1
option B and the reason FR-016 says "nothing that identifies the document to the service may
survive".

---

## Transport model changes (`models.py`)

Nothing here is persisted; these are response shapes.

### `JobSummary` gains one field

| Field | Type | Meaning |
|---|---|---|
| `content_hash` | `str` | The document this conversion belongs to. Two rows sharing it are two conversions of the same PDF and are deleted together (FR-021) |

This is the identifier the page needs to reason about siblings at all (research.md R5). It is one
64-character string per row on a payload that already carries a filename, a display status, and
up to two timestamps, on a poll capped at 500 rows.

### `JobDetail` gains two fields

| Field | Type | Meaning |
|---|---|---|
| `document_outputs` | `list[OutputFile]` | Every file recorded for this `content_hash`, whichever conversion wrote it. What a deletion will remove (FR-017) |
| `retained_upload` | `bool` | Whether the uploaded PDF is still on the server and will be discarded with it (FR-016) |

`outputs` is left exactly as it is — the files *this job* wrote — so the feature-001 contract and
its tests continue to hold. The two differ for an `already_converted` job, whose `markdown_output`
rows carry the original job's id: `outputs` is empty for it, `document_outputs` is not. The
confirmation must use `document_outputs`, or deleting from an `already_converted` row would claim
to remove nothing while removing every section file of the document.

### `DeletionResult` — what `DELETE /api/jobs/{job_id}` returns

| Field | Type | Meaning |
|---|---|---|
| `job_ids` | `list[str]` | Every list entry removed, so the page can drop them all |
| `filename` | `str` | For the confirmation message |
| `removed_files` | `list[str]` | Outbox files actually unlinked |
| `kept_files` | `list[str]` | Outbox files that could not be removed — empty on a clean deletion |
| `upload_discarded` | `bool` | Whether a retained upload was found and removed |

`kept_files` is what makes FR-018 testable: a partial failure is reported as a partial failure,
naming what survived, rather than as a success or as an opaque 500.

---

## Validation rules

| Rule | Source | Behaviour |
|---|---|---|
| Job must exist | FR-020, R10 | 404 `already_deleted` on delete, which the page treats as success |
| No conversion of the document in flight | FR-019, FR-022 | 409 `still_converting`, message naming the document |
| Outbox unwritable | FR-018 | Rows still removed, `kept_files` populated, 200 with a message the page shows as a partial outcome |
| Deletion is one document per request | FR-026 | No bulk route, no request body, no query parameter that widens the blast radius |

---

## Query surface

`GET /api/jobs` gains a `content_hash` filter beside its existing `batch_id`, `status`, and
`since`. The page calls it once, when the operator chooses Delete, to count exactly how many
entries will disappear. It is not on the polling path.

No index is added for it. `conversion_job` holds hundreds of rows pruned at
`PDF2MD_JOB_HISTORY_DAYS`, the filter runs once per deletion rather than every two seconds, and
`DELETE ... WHERE content_hash = ?` scans the same small table. An index here would be weight
carried for a query that is already fast — revisit if history retention is ever lengthened by an
order of magnitude.

## Still no schema migration

Worth restating after those additions: `content_hash` is already a column on `conversion_job`,
`document_outputs` is `outputs_for_hash` unfiltered, and `retained_upload` is a stat call. Nothing
in `SCHEMA` changes.
