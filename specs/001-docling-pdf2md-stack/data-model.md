# Data Model: Offline Docling PDF-to-Markdown Stack

**Feature**: `001-docling-pdf2md-stack` | **Date**: 2026-08-18 | **Plan**: [plan.md](./plan.md)

All state lives in one SQLite database on a named volume, plus two filesystem locations (inbox, outbox). There is a single writer — the web service — so no external database is needed at this scale.

---

## Entities

### Batch

One upload action from the browser page. Groups the documents a user submitted together (FR-009) so the page can show aggregate progress.

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUIDv4 |
| `created_at` | TEXT | ISO-8601 UTC |
| `document_count` | INTEGER | Number of files accepted in this upload |
| `submitter_note` | TEXT NULL | Optional free text from the page; never used for routing or auth |

A batch is derived, not authoritative: deleting a batch row must not orphan jobs, so `conversion_job.batch_id` is nullable.

### SourceDocument

A PDF accepted for conversion. Identified by content, not by filename — this is what makes naming and deduplication deterministic (research.md R8).

| Field | Type | Notes |
|---|---|---|
| `content_hash` | TEXT PK | SHA-256 of the PDF bytes, lowercase hex |
| `original_filename` | TEXT | As supplied by the browser, sanitized for display only |
| `size_bytes` | INTEGER | |
| `page_count` | INTEGER NULL | Populated from the engine result when available |
| `first_seen_at` | TEXT | ISO-8601 UTC |
| `inbox_path` | TEXT NULL | Path in the inbox volume; NULL once the PDF has been reaped |

**Rules**
- Two uploads of identical bytes converge on one `SourceDocument`, even under different filenames.
- The same bytes uploaded twice under different names keep the first `original_filename`; the second upload's name is recorded on its own job for traceability.
- Rejected before a row is created: files that are not PDFs by magic bytes, files over `MAX_UPLOAD_BYTES`, and zero-byte files (FR-007).

### ConversionJob

One attempt to convert one `SourceDocument`. The unit the page displays and the operator reasons about.

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUIDv4 |
| `batch_id` | TEXT NULL FK → `batch.id` | |
| `content_hash` | TEXT FK → `source_document.content_hash` | |
| `submitted_filename` | TEXT | Name used in *this* submission |
| `status` | TEXT | See state machine below |
| `engine_task_id` | TEXT NULL | `task_id` from the engine |
| `queue_position` | INTEGER NULL | Last `task_position` seen from the engine, for display only |
| `created_at` | TEXT | |
| `started_at` | TEXT NULL | When the engine reported `started` |
| `ended_at` | TEXT NULL | Terminal transition time |
| `attempt` | INTEGER | 1 on first try; incremented on restart-driven resubmission |
| `failure_reason` | TEXT NULL | Human-readable, shown verbatim on the page (FR-011) |
| `engine_errors` | TEXT NULL | JSON array copied from the engine's `errors[]`, for logs |
| `output_filename` | TEXT NULL | Set on success; see `MarkdownOutput` |

**Rules**
- `failure_reason` must be readable by a non-technical user. Engine stack traces go to `engine_errors` and the container log, never to this field.
- A job never transitions out of a terminal state. A re-run creates a new job against the same `SourceDocument`.
- Jobs are retained for `JOB_HISTORY_DAYS` (default 30), then pruned. Pruning a job never deletes its `MarkdownOutput` — the outbox is the durable record (FR-013).

### MarkdownOutput

The result of a successful conversion, as written to the outbox.

| Field | Type | Notes |
|---|---|---|
| `output_filename` | TEXT PK | `{slug}--{content_hash[:12]}.md` |
| `content_hash` | TEXT FK → `source_document.content_hash` | |
| `job_id` | TEXT FK → `conversion_job.id` | Job that produced the current file |
| `bytes` | INTEGER | Size of the Markdown |
| `written_at` | TEXT | |
| `engine_status` | TEXT | `success` or `partial_success` from the engine |

**Rules**
- The filename is a pure function of content hash and original name, so re-converting the same PDF overwrites in place rather than accumulating duplicates for AnythingLLM to ingest twice (FR-014).
- Written atomically: to a temp file in the outbox, then renamed. A power loss mid-write can leave a temp file but never a truncated `.md` that looks complete.
- No file is written for a failed job (FR-007, User Story 4 scenario 4).

---

## Job state machine

```text
                    ┌──────────────────┐
   upload accepted  │                  │  identical content already
   ────────────────▶│      queued      │──────converted, output present─────▶ already_converted ●
                    │                  │
                    └────────┬─────────┘
                             │ submitted to engine, task_id assigned
                             ▼
                    ┌──────────────────┐
                    │    submitted     │◀──── engine reports task_status=pending
                    └────────┬─────────┘
                             │ engine reports task_status=started
                             ▼
                    ┌──────────────────┐
                    │     running      │
                    └────┬────┬────┬───┘
        engine success   │    │    │  engine failure / result unreadable
        result persisted │    │    └──────────────────────────▶ failed ●
                         │    │
                         │    └── exceeded JOB_TIMEOUT_SECONDS ──▶ timed_out ●
                         ▼
                    succeeded ●        (engine_status may be success or partial_success)
                         │
                         └── yield below suspect threshold ──▶ succeeded_suspect ●
```

Terminal states are marked ●: `succeeded`, `succeeded_suspect`, `already_converted`, `failed`, `timed_out`.

**Status vocabulary shown to users** (FR-010): queued → *Queued*; submitted/running → *Converting*; succeeded → *Converted*; succeeded_suspect → *Converted — check output*; already_converted → *Already converted*; failed → *Failed*; timed_out → *Timed out*.

### Transition rules

| From | To | Trigger | Side effects |
|---|---|---|---|
| — | `queued` | Upload accepted and stored in inbox | `SourceDocument` created or reused |
| `queued` | `already_converted` | A `MarkdownOutput` exists for this `content_hash` **and** the file is present in the outbox | No engine work; `output_filename` copied onto the job |
| `queued` | `submitted` | Dispatcher posts to the engine and receives `task_id` | |
| `submitted` | `running` | Poll returns `task_status=started` | `started_at` set |
| `submitted`/`running` | `succeeded` | Poll returns `success`, result fetched and persisted | Markdown written to outbox; inbox PDF eligible for reaping |
| `submitted`/`running` | `succeeded_suspect` | Result persisted, but yield is below the suspect threshold | Markdown written to the outbox as normal; only the reported state differs (FR-029) |
| `submitted`/`running` | `failed` | Poll returns `failure`, or the result fetch/persist fails | `failure_reason` set |
| `submitted`/`running` | `timed_out` | Wall clock since `created_at` exceeds `JOB_TIMEOUT_SECONDS` | Job abandoned; the engine's own `MAX_DOCUMENT_TIMEOUT` is set lower so the engine gives up first |
| `queued`/`submitted`/`running` | `queued` (attempt+1) | Service restart, inbox PDF still present | Engine task IDs do not survive an engine restart, so the job is resubmitted rather than polled |
| `queued`/`submitted`/`running` | `failed` | Service restart, inbox PDF missing | `failure_reason` = "interrupted by a restart and the uploaded file is no longer available" |

The restart rules are what satisfy User Story 5 scenario 3: nothing in flight is ever left silently in a non-terminal state after a restart — it is either genuinely resumed or explicitly reported.

### Suspect-yield detection (FR-029)

A successful conversion is flagged `succeeded_suspect` when the Markdown contains fewer than `SUSPECT_MIN_CHARS_PER_PAGE` (default 50) characters per source page, or is empty. Page count comes from the engine result; when it is unavailable, a flat floor of 200 characters applies.

The output is **always written** — this is a reporting distinction, not a failure. A genuinely blank source scan legitimately lands here, which is the intended outcome: the user is told the result looks empty and can judge for themselves, rather than importing an empty file believing it converted.

The threshold is a tuning knob, not a contract. Adjust it from measured corpus results (see the fidelity harness in tasks T089–T091) rather than by intuition.

### The single-use result hazard

The engine serves each result **once** (research.md R3). Fetching and persisting is therefore one indivisible step:

1. `GET /v1/result/{task_id}`
2. Write Markdown to a temp file in the outbox, `fsync`, rename into place
3. In one SQLite transaction: insert/replace `MarkdownOutput`, set job `succeeded`

If step 2 or 3 fails, the job goes to `failed` with a reason naming the lost result. It is never left as `running`, because a second fetch would return nothing.

---

## Schema

```sql
CREATE TABLE batch (
  id              TEXT PRIMARY KEY,
  created_at      TEXT NOT NULL,
  document_count  INTEGER NOT NULL,
  submitter_note  TEXT
);

CREATE TABLE source_document (
  content_hash      TEXT PRIMARY KEY,
  original_filename TEXT NOT NULL,
  size_bytes        INTEGER NOT NULL,
  page_count        INTEGER,
  first_seen_at     TEXT NOT NULL,
  inbox_path        TEXT
);

CREATE TABLE conversion_job (
  id                 TEXT PRIMARY KEY,
  batch_id           TEXT REFERENCES batch(id) ON DELETE SET NULL,
  content_hash       TEXT NOT NULL REFERENCES source_document(content_hash),
  submitted_filename TEXT NOT NULL,
  status             TEXT NOT NULL CHECK (status IN
                       ('queued','submitted','running','succeeded','succeeded_suspect',
                        'already_converted','failed','timed_out')),
  engine_task_id     TEXT,
  queue_position     INTEGER,
  created_at         TEXT NOT NULL,
  started_at         TEXT,
  ended_at           TEXT,
  attempt            INTEGER NOT NULL DEFAULT 1,
  failure_reason     TEXT,
  engine_errors      TEXT,
  output_filename    TEXT REFERENCES markdown_output(output_filename)
);

CREATE TABLE markdown_output (
  output_filename TEXT PRIMARY KEY,
  content_hash    TEXT NOT NULL REFERENCES source_document(content_hash),
  job_id          TEXT NOT NULL,
  bytes           INTEGER NOT NULL,
  written_at      TEXT NOT NULL,
  engine_status   TEXT NOT NULL
);

CREATE INDEX idx_job_status  ON conversion_job(status);
CREATE INDEX idx_job_created ON conversion_job(created_at DESC);
CREATE INDEX idx_job_batch   ON conversion_job(batch_id);
CREATE INDEX idx_output_hash ON markdown_output(content_hash);
```

`PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` are set on every connection. WAL is safe here only because the database is on a named volume, not a macOS bind mount (plan.md, research.md R7).

---

## Filesystem layout

| Location | Mount type | Contents | Lifecycle |
|---|---|---|---|
| `/data/db` | Named volume | `pdf2md.sqlite` + WAL files | Survives redeploy (FR-017) |
| `/data/inbox` | Named volume | Uploaded PDFs, named by content hash | Reaped `INBOX_RETENTION_HOURS` (default 48) after a job succeeds. Jobs ending `failed` or `timed_out` keep their PDF for `FAILED_INBOX_RETENTION_DAYS` (default 14) so they can be retried, then it is reaped too |
| `/data/outbox` | **Bind mount** to a host directory | `*.md` — the durable record and the AnythingLLM handoff (FR-013) | Never auto-deleted; the operator manages it |

The outbox is the one location the operator opens in Finder, which is why it is a bind mount despite the macOS filesystem bridge — plain sequential file writes are safe there, unlike SQLite's locking.

---

## Derived views the page needs

| View | Query shape | Serves |
|---|---|---|
| Recent jobs | `conversion_job` joined to `source_document`, ordered by `created_at DESC`, limit N | FR-010 live status list |
| Batch progress | Count by `status` grouped by `batch_id` | User Story 5 batch visibility |
| Backlog depth | Count where `status IN ('queued','submitted','running')` | Health display, FR-027 evidence |
| Outbox inventory | `markdown_output` ordered by `written_at DESC` | Operator's import checklist (User Story 4) |
