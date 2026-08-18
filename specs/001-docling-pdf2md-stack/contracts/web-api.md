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

**Rejection reasons** (FR-007): not a PDF by magic bytes; larger than `MAX_UPLOAD_BYTES`; zero bytes; password-protected or unreadable structure where detectable at upload time. Rejections never create a job.

**413** when the whole request body exceeds the server limit. **507** when the outbox or inbox has no space left, with a message naming which.

---

## `GET /api/jobs`

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
      "page_count": null,
      "failure_reason": null,
      "output_filename": null,
      "download_url": null
    }
  ]
}
```

`status` is the machine vocabulary from [data-model.md](../data-model.md); `display_status` is the user-facing string, so the page never maps states itself — `succeeded_suspect` renders as *Converted — check output* (FR-029). `download_url` is populated for `succeeded`, `succeeded_suspect`, and `already_converted`.

---

## `GET /api/jobs/{job_id}`

**200** — a single job object as above, plus:

```json
{
  "engine_status": "partial_success",
  "engine_errors": ["Page 14: table structure could not be resolved"],
  "processing_seconds": 96.4,
  "output_bytes": 51233,
  "content_hash": "4f2a91b0c7d3…"
}
```

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
