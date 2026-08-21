# Contract: Browser-Facing Web API

**Feature**: `001-docling-pdf2md-stack` | **Consumer**: the single operations page (and any LAN client)

Base URL: `http://<mac-mini-lan-ip>:8080`. No authentication — presence on the local network is the authorization (FR-024). Every response is JSON except the page assets and the Markdown download.

## Conventions

- Timestamps are ISO-8601 UTC with a `Z` suffix.
- Errors use one shape:
  ```json
  { "error": { "code": "file_too_large", "message": "\"report.pdf\" is 220 MB. The limit is 200 MB." } }
  ```
  `message` is written for a non-technical reader and is safe to display verbatim (FR-011).
- No endpoint requires a header beyond what a browser sends by default.

---

## `GET /`

Serves the operations page. All assets (`app.js`, `styles.css`, any fonts or icons) are served from this origin — no CDN, no external font, no analytics (FR-025).

**200** `text/html`

---

## `POST /api/uploads`

Accepts one or more PDFs (FR-008, FR-009).

**Request**: `multipart/form-data`

| Field | Cardinality | Notes |
|---|---|---|
| `files` | 1..N | Each part is a PDF |
| `note` | 0..1 | Optional free text recorded on the batch |

**202 Accepted** — the batch was created. Per-file acceptance is reported individually; a rejected file does not fail the batch.

```json
{
  "batch_id": "9f1c…",
  "accepted": [
    { "job_id": "3ab…", "filename": "annual-report.pdf", "status": "queued" },
    { "job_id": "7cd…", "filename": "scan.pdf", "status": "already_converted",
      "output_filename": "scan--4f2a91b0c7d3.md" }
  ],
  "rejected": [
    { "filename": "notes.txt",
      "reason": "Not a PDF. Only PDF files can be converted." }
  ]
}
```

**Rejection reasons** (FR-007, FR-036): not a PDF by magic bytes; larger than `MAX_UPLOAD_BYTES`; zero bytes; password-protected; unreadable page structure; **more pages than `MAX_TOTAL_PAGES`**. Rejections never create a job.

Since the page count is read at upload (research.md R11), the last three are now decided in the moment rather than after a round trip through the engine. A document that is merely long is never described as damaged — it is refused for its length, with what to do about it.

**413** when the whole request body exceeds the server limit. **507** when the outbox or inbox has no space left, with a message naming which.

---

## `GET /api/jobs`

> **Extended by feature 002.** Every summary now carries `content_hash`, and the endpoint accepts a
> `content_hash` filter. `GET /api/jobs/{job_id}` gains `document_outputs` and `retained_upload`,
> and `DELETE /api/jobs/{job_id}` is new. See
> [`specs/002-job-list-layout-delete/contracts/web-api-deletion.md`](../../002-job-list-layout-delete/contracts/web-api-deletion.md).

The list the page polls (FR-010). Returns most recent first.

| Query param | Default | Notes |
|---|---|---|
| `limit` | 100 | Max 500 |
| `batch_id` | — | Restrict to one upload |
| `status` | — | Repeatable filter |
| `since` | — | ISO-8601; return only jobs changed after this, for cheap polling |

**200**

```json
{
  "server_time": "2026-08-18T14:22:31Z",
  "backlog": { "queued": 3, "converting": 2 },
  "jobs": [
    {
      "job_id": "3ab…",
      "batch_id": "9f1c…",
      "filename": "annual-report.pdf",
      "status": "running",
      "display_status": "Converting",
      "queue_position": null,
      "created_at": "2026-08-18T14:20:02Z",
      "started_at": "2026-08-18T14:20:44Z",
      "ended_at": null,
      "attempt": 1,
      "size_bytes": 8412233,
      "page_count": 2413,
      "part_count": 25,
      "parts_completed": 7,
      "missing_page_ranges": null,
      "failure_reason": null,
      "output_filename": null,
      "download_url": null
    }
  ]
}
```

`status` is the machine vocabulary from [data-model.md](../data-model.md); `display_status` is the user-facing string, so the page never maps states itself — `succeeded_suspect` renders as *Converted — check output* (FR-029), and a split document in progress renders as *Converting — part 7 of 25* (FR-037). `download_url` is populated for `succeeded`, `succeeded_suspect`, `succeeded_incomplete`, and `already_converted`.

`page_count` is known from upload onwards, not only after conversion (FR-036). `part_count` is 1 for a document converted whole, so the page needs no special case: it shows the part counter only when `part_count > 1`. `missing_page_ranges` is non-null only for `succeeded_incomplete`, and carries the ranges whose parts failed — e.g. `[[901, 1000]]` (FR-035).

---

## `GET /api/jobs/{job_id}`

**200** — a single job object as above, plus:

```json
{
  "engine_status": "partial_success",
  "engine_errors": ["Page 14: table structure could not be resolved"],
  "processing_seconds": 96.4,
  "output_bytes": 51233,
  "content_hash": "4f2a91b0c7d3…",
  "outputs": [
    { "filename": "manual--4f2a91b0c7d3--001-installation.md",
      "section_title": "Installation", "bytes": 44120 },
    { "filename": "manual--4f2a91b0c7d3--002-configuration.md",
      "section_title": "Configuration", "bytes": 71880 }
  ],
  "missing_parts": [
    { "first_page": 901, "last_page": 910, "status": "timed_out", "attempts": 1,
      "failure_reason": "These pages were still converting after the time limit…" }
  ]
}
```

`outputs` lists every file this document wrote. It holds one entry for an ordinary
document and one per section for a document over the section threshold (FR-033), so the
detail view can show what an operator will actually find in the outbox rather than a
single name that no longer describes it.

`succeeded_incomplete` is the one finished state `POST /api/jobs/{job_id}/retry` accepts:
the file exists and is missing pages, so there is something to retry (FR-040). Every other
downloadable state is still refused with `409 already_converted`. The retry is a whole new
conversion of the document, and its output replaces the incomplete one in place.

`missing_parts` is the detail behind `missing_page_ranges`: one entry per range that is
absent from the finished document, with the reason the engine gave and how many attempts
were made before it was accepted as a gap (FR-038). Empty for every other job. Without it
a gap is the same sentence whether the engine ran out of time, lost the task, or found the
pages unreadable — and only some of those have an answer the operator can act on.

`GET /api/health` carries a `dispatcher` block alongside `engine`:

```json
{ "running": true, "last_pass_at": "…Z", "last_engine_error": null, "last_engine_error_at": null }
```

`engine.reachable` answers whether `/ready` responds; this answers whether work is moving.
They differ: an engine that answers `/ready` can refuse every submission, and the loop can
stop while everything it depends on stays healthy. Either makes the status `degraded`, so a
queue that never empties is never reported as a converter standing ready (FR-041).

**404** when the job has been pruned from history (`JOB_HISTORY_DAYS`). The Markdown itself remains in the outbox — history pruning never removes output.

---

## `GET /api/jobs/{job_id}/markdown`

Retrieves the converted Markdown in the browser (FR-012).

**200** `text/markdown; charset=utf-8`, with
`Content-Disposition: attachment; filename="annual-report--4f2a91b0c7d3.md"`

The filename matches the outbox filename exactly, so a user download and an operator's outbox copy are the same artifact under the same name.

**404** when the job did not succeed, or the outbox file has been removed by the operator. **409** when the job is not yet terminal, with a message saying the document is still converting.

---

## `POST /api/jobs/{job_id}/retry`

Creates a **new** job for the same source document. Jobs are immutable once terminal (data-model.md), so this never mutates the original.

**202** `{ "job_id": "new-id", "status": "queued" }`

**409** when the original job succeeded (nothing to retry) or when the uploaded PDF has already been reaped from the inbox, with a message asking the user to upload the file again.

---

## `GET /api/health`

Operator-facing detail, also rendered on the page.

**200**

```json
{
  "status": "ok",
  "engine": { "reachable": true, "checked_at": "2026-08-18T14:22:30Z" },
  "backlog": { "queued": 3, "converting": 2 },
  "outbox": { "writable": true, "free_bytes": 41203847168, "documents": 214 },
  "database": { "writable": true },
  "version": "1.0.0"
}
```

**503** with the same shape and `"status": "degraded"` when the engine is unreachable or a storage location is unwritable. Uploads are still accepted while degraded — they queue until the engine returns.

---

## `GET /healthz`

Container healthcheck target. Cheap, no engine call, no database write.

**200** `{"status":"ok"}` once the app has started and the database is readable.

---

## Behavioral guarantees the page relies on

| Guarantee | Backing requirement |
|---|---|
| A job appears in `GET /api/jobs` immediately after `POST /api/uploads` returns | FR-010 — status is visible without a reload |
| Status transitions are monotonic; a job never moves backwards out of a terminal state | data-model.md state machine |
| `failure_reason` is present and human-readable on every `failed`/`timed_out` job | FR-011 |
| `download_url` is present for exactly the successful jobs | FR-012 |
| Polling `GET /api/jobs?since=…` is cheap enough for a 2-second interval with several clients connected | FR-010, SC-011 |
