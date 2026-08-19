# Contract: Upstream `docling-serve` Engine (consumed)

**Feature**: `001-docling-pdf2md-stack` | **Direction**: the web service is the client; the engine is upstream software we do not modify

Reachable only at `http://docling:5001` on the `core` internal network. Never published to the LAN.

## Endpoints used

| Step | Call | Notes |
|---|---|---|
| Submit | `POST /v1/convert/file/async` | multipart; `files`, `from_formats=pdf`, `to_formats=md`, `do_ocr=true` |
| Poll | `GET /v1/status/poll/{task_id}` | returns `task_status`, `task_position` |
| Fetch | `GET /v1/result/{task_id}` | returns the document payload |
| Health | `GET /ready` | **RESOLVED (O1)** — verified against the pinned tag's source: `/ready` answers 503 until the models are loaded, `/health` only reports that the process is up, and neither requires the API key. The container healthcheck and our own reachability check both use `/ready`; `/livez` and `/version` also exist. |

All requests carry `X-Api-Key: ${DOCLING_SERVE_API_KEY}` (research.md R9).

## Payload shapes

**Submission and poll response**

```jsonc
{
  "task_id": "…",
  "task_status": "pending",   // pending | started | success | failure
  "task_position": 1,
  "task_meta": null
}
```

**Result**

```jsonc
{
  "document": {
    "md_content": "# Title\n\n…",
    "json_content": {},
    "html_content": "",
    "text_content": "",
    "doctags_content": ""
  },
  "status": "success",        // success | partial_success | skipped | failure
  "processing_time": 96.4,
  "timings": {},
  "errors": []
}
```

Only `md_content` is requested, so the other content fields come back empty. That is expected, not an error.

## Mapping onto our job states

| Engine signal | Our state | Notes |
|---|---|---|
| `task_status=pending` | `submitted` | `task_position` surfaces as the queue position |
| `task_status=started` | `running` | sets `started_at` |
| `task_status=success` + `status=success` | `succeeded` | `engine_status=success` |
| `task_status=success` + `status=partial_success` | `succeeded` | `engine_status=partial_success`; `errors[]` retained and shown on the detail view (research.md O4) |
| `task_status=success` + `status=skipped\|failure` | `failed` | `failure_reason` derived from `errors[]`, rewritten for a non-technical reader |
| `task_status=failure` | `failed` | |
| No terminal status before `JOB_TIMEOUT_SECONDS` | `timed_out` | our watchdog; the engine's `MAX_DOCUMENT_TIMEOUT` is set lower so it normally gives up first |
| Connection refused / engine restarting | job stays in place, retried | health goes `degraded`; no state change on the job |

## Hazards this contract imposes

1. **Single-use results.** `DOCLING_SERVE_SINGLE_USE_RESULTS` defaults to `true` and results are removed `DOCLING_SERVE_RESULT_REMOVAL_DELAY` (300s) after being read. `GET /v1/result/{task_id}` is therefore called exactly once per job, and its payload is persisted before anything else can fail. A failure after the fetch marks the job `failed` — it is never retried against the same `task_id`.

2. **Task IDs do not survive an engine restart.** On startup the web service does not attempt to poll stored `engine_task_id` values; it resubmits from the inbox instead (data-model.md restart rules).

3. **`to_formats` defaults to `md`** but is always sent explicitly, so an upstream default change cannot silently alter our output.

4. **Sync endpoints are unusable here.** `DOCLING_SERVE_MAX_SYNC_WAIT` is 120s by default — shorter than a large PDF conversion. Only the async endpoints are used.

## Version pinning

The engine image is pinned to an exact tag, never `latest`, and the tag is recorded in `deploy/.env`. Upgrading is a deliberate act: pull and verify on a connected machine, re-run the air-gap transfer, redeploy. Any upgrade must re-verify that the artifacts directory in the new image is populated (research.md R4) — `docling-serve-slim` variants, which skip model weights, are incompatible with this deployment.
