# Phase 0 Research: Offline Docling PDF-to-Markdown Stack

**Feature**: `001-docling-pdf2md-stack` | **Date**: 2026-08-18 | **Plan**: [plan.md](./plan.md)

Findings that resolve the unknowns in the plan's Technical Context. Items marked **VALIDATED** were tested on the target host during planning; items marked **VERIFY AT IMPLEMENTATION** are documented decisions that still need a runtime check.

---

## R1. Network isolation: how to be egress-free and LAN-reachable at once

**Decision**: Two Docker networks.

- `core` — `internal: true`. The conversion engine sits here alone. No default route exists, so egress is impossible by construction.
- `edge` — a bridge with `com.docker.network.bridge.enable_ip_masquerade: "false"`. The web service sits on both `edge` and `core`. Its port is published to the LAN; its egress has no SNAT and therefore blackholes.

**Rationale**: The obvious approach — put everything on an `internal` network — was tested first and **breaks inbound**. On the target host (OrbStack 29.4.0, linux/arm64):

| Topology | LAN inbound on published port | Container egress to 1.1.1.1 |
|---|---|---|
| `internal: true` only | **HTTP 000 (fails)** | Blocked — "Network unreachable", no default route |
| bridge, masquerade disabled | **HTTP 200** from `10.0.0.19:18081` | Blocked — times out, no SNAT for return path |
| Final: web on `edge`+`core`, engine on `core` | **HTTP 200** from LAN | web BLOCKED, engine BLOCKED |

**VALIDATED** — the final two-network compose topology was deployed and torn down on this host. Confirmed in one run: LAN inbound to web returned HTTP 200; `web → engine` resolved by service name and returned HTTP 200 over `core`; egress blocked from both services; the engine published no ports.

**Alternatives considered**:
- *Single `internal` network*: rejected, published ports do not work (measured above).
- *Host firewall rules (pf) on the Mac mini*: rejected as the primary control — it lives outside the stack definition, so a Portainer redeploy cannot carry it, violating the "deploy from one stack definition" requirement (FR-015).
- *Accepting egress capability on the web service and relying only on behavioral verification*: rejected as the primary control, since FR-021 is a standing property, not a one-time observation. Behavioral verification (FR-026) is retained as a second layer.

**Residual gap to document**: `enable_ip_masquerade=false` blocks internet egress but does not blackhole traffic to the Docker host itself, which still has a route back to the bridge subnet. The engine on `core` has no such gap. This is why the engine — the only component that would ever attempt a model download — is the one placed on the fully internal network.

---

## R2. Conversion engine: upstream `docling-serve` rather than embedding the library

**Decision**: Run the upstream `docling-serve-cpu` image as the conversion engine and talk to it over the `core` network. Do not embed the Docling Python library in a bespoke service.

**Rationale**: `docling-serve` already provides, as configuration, most of what the spec's non-conversion requirements demand:

| Spec requirement | Provided by |
|---|---|
| FR-027 bounded concurrent work | `DOCLING_SERVE_ENG_LOC_NUM_WORKERS` (default 2), `DOCLING_SERVE_QUEUE_MAX_SIZE` |
| FR-028 per-document timeout | `DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT` (default 604800s — must be lowered) |
| FR-021/FR-022 no runtime downloads | `DOCLING_SERVE_ARTIFACTS_PATH`, already set in the image to `/opt/app-root/src/.cache/docling/models`, populated at image build time |
| FR-021 no outbound model/API calls | `DOCLING_SERVE_ENABLE_REMOTE_SERVICES=false`, `DOCLING_SERVE_ALLOW_EXTERNAL_PLUGINS=false` |
| Upload bounds | `DOCLING_SERVE_MAX_FILE_SIZE`, `DOCLING_SERVE_MAX_NUM_PAGES` |

Reimplementing a queue, worker pool, and timeout supervisor around the raw library would duplicate all of this and own the failure modes ourselves.

**Alternatives considered**:
- *Single container embedding the `docling` library with an in-process worker pool*: rejected. Fewer moving parts on paper, but it makes us responsible for queueing, model warm-up, per-document timeouts, and memory isolation of CPU-heavy conversions — all of which the upstream service already solves.
- *`docling-serve`'s built-in Gradio UI (`DOCLING_SERVE_ENABLE_UI=1`)*: rejected as the user-facing page. It is described upstream as a demonstrator; it does not provide the durable job history (FR-017), deterministic output naming (FR-014), or the output-folder handoff (FR-013) the spec requires. Its cache is also swept hourly by Gradio, deleting results older than ten hours, which conflicts with retention expectations. It stays disabled.

---

## R3. `docling-serve` API surface used

**Decision**: Use the async task API, not the synchronous endpoints.

| Step | Call |
|---|---|
| Submit | `POST /v1/convert/file/async` (multipart; fields `files`, `from_formats=pdf`, `to_formats=md`, `do_ocr=true`) |
| Poll | `GET /v1/status/poll/{task_id}` → `task_status` in `pending \| started \| success \| failure`, plus `task_position` |
| Fetch | `GET /v1/result/{task_id}` → `document.md_content`, `status`, `errors[]`, `processing_time` |

**Rationale**: Synchronous conversion is capped by `DOCLING_SERVE_MAX_SYNC_WAIT` (120s default), well below the runtime of a large PDF. The async API also surfaces `task_position`, which feeds the queued/running distinction the page must show (FR-010).

**Critical behavior**: `DOCLING_SERVE_SINGLE_USE_RESULTS` defaults to `true` — a result can be read **once**, then is removed after `DOCLING_SERVE_RESULT_REMOVAL_DELAY` (300s). The web service must therefore persist `md_content` to the outbox and the job record in the same operation that fetches it, and must never rely on re-reading a result. A failure between fetch and persist loses the conversion and requires a re-run; the job is marked failed rather than silently lost.

**Alternatives considered**: the websocket endpoint `/v1/status/ws/{task_id}` and `callbacks` webhooks. Both rejected for v1 — polling a handful of concurrent tasks at a small interval is sufficient at this scale and avoids a second connection lifecycle to manage.

---

## R4. Offline model provisioning

**Decision**: Ship the stock `docling-serve-cpu` image unmodified. Its Containerfile pre-downloads the model set at build time into `DOCLING_SERVE_ARTIFACTS_PATH=/opt/app-root/src/.cache/docling/models` and sets `TESSDATA_PREFIX` for Tesseract data. No derived image, no model volume.

**Rationale**: The models are already inside the published image, which is exactly what FR-022 asks for. Keeping the image stock means air-gap transfer is a plain `docker save`/`docker load` with nothing to reassemble on the far side.

**OCR engine**: Take the image default. Upstream's `docling-tools models download` output shows RapidOCR weights (torch and onnxruntime, English and Chinese) fetched as part of the default set, so scanned-page recognition works offline with no extra assets. EasyOCR language packs are the exception — they are downloaded only on explicit request (`docling-tools models download easyocr --easyocr-lang ...`) and so are unavailable unless baked in. This matches the spec's English-only assumption.

**VERIFY AT IMPLEMENTATION**: confirm the pulled tag actually contains a populated artifacts directory before air-gapping it —
`docker run --rm --entrypoint sh <image> -c 'ls /opt/app-root/src/.cache/docling/models'`. Upstream has teased `docling-serve-slim` images that *skip* model weights; those must never be used here.

**Alternatives considered**: mounting a host directory of models populated by `docling-tools models download` on a connected machine. Rejected as redundant given the image already carries them, and it adds a second artifact to keep in sync with the image version.

---

## R5. Air-gapped image delivery

**Decision**: Build/pull on a connected machine, `docker save | gzip`, transfer the archive out of band, `docker load` on the Mac mini, and pin the stack to exact tags with `pull_policy: never`.

**Rationale**: A Portainer stack normally pulls from a registry; with no egress that fails. `pull_policy: never` makes the stack fail fast and loudly if an image is missing locally, instead of hanging on a pull timeout. Portainer's own "re-pull image" toggle must also stay off at deploy time, since it overrides compose behavior.

**Sizes to plan for**: upstream lists `docling-serve-cpu` at ~4.4 GB for arm64. The custom web image on `python:3.12-slim` is on the order of 200 MB. Both must be transferred; the base image is not present on an air-gapped host either.

**VALIDATED**: `pull_policy: never` parses and is honored by the compose implementation on this host (29.4.0).

---

## R6. Host platform reality

**Decision**: Target `linux/arm64`, CPU-only, and size the engine for the container VM's memory rather than the Mac's.

**Measured on the target host**: Apple M4, 16 GB physical; the container VM reports 10 CPUs and **8.38 GB** of RAM. The runtime is **OrbStack** 29.4.0, not Docker Desktop, and `portainer/portainer-ee` is already present locally — consistent with this being the Mac mini described in the spec.

**Implications**:
- No GPU or MPS is reachable from Linux containers on macOS. `DOCLING_DEVICE=cpu` is set explicitly rather than left on `auto`.
- 8.38 GB across the whole VM is the real budget, and Portainer plus any other stacks share it. Engine memory is capped (`mem_limit`) and `DOCLING_SERVE_ENG_LOC_SHARE_MODELS=true` is set so worker threads share one model set instead of each holding a copy. This is what protects SC-011 (host stays responsive).
- `OMP_NUM_THREADS` is left at the image default of 4, leaving headroom on the 10 available CPUs for the web service and Portainer.

**VERIFY AT IMPLEMENTATION**: measured RSS of the engine at 2 workers with `SHARE_MODELS=true`, to confirm the chosen `mem_limit` is neither throttling conversions nor starving the host.

---

## R7. Web service stack

**Decision**: Python 3.12 + FastAPI + `httpx` + `sqlite3`, serving a dependency-free vanilla HTML/CSS/JS page from the same container.

**Rationale**:
- Same language as Docling, so the operator debugs one runtime.
- FR-025 requires the page to work for a client with no internet: no CDN, no web fonts, no external analytics. A no-build-step vanilla page makes that a property of the source tree rather than a bundler configuration to audit. It also means no Node toolchain in the air-gap transfer.
- SQLite covers the durable job registry (FR-017) with no additional container.

**Status transport**: the page polls `GET /api/jobs` on a short interval. Server-Sent Events were considered and rejected for v1 — polling at this scale is simpler and reconnects for free, and FR-010 requires only that status updates without user action.

**SQLite placement**: the database lives on a **named Docker volume**, never on a macOS bind mount. SQLite's locking is unreliable across the macOS-to-VM filesystem bridge. The outbox, which the operator needs to reach from Finder for the manual AnythingLLM import, is a bind mount — plain file writes there are safe.

**Alternatives considered**: Postgres (rejected — a third container and a daemon to operate for a single-writer workload of tens of rows per day); a React/Vite frontend (rejected — a build toolchain and node_modules to move across the air gap for one page).

---

## R8. Output naming

**Decision**: `{slug-of-original-filename}--{sha256-of-pdf-bytes[:12]}.md`.

**Rationale**: FR-014 demands names that are stable, unique, and free of ambiguous duplicates for the person importing into AnythingLLM. Content addressing gives all three: re-converting the same PDF produces the same name and overwrites in place, so no duplicate reaches AnythingLLM; two different documents that share a filename get different names; and the slug keeps it human-recognizable. The content hash is also what lets the page say "this exact document was already converted" instead of silently queueing duplicate work.

**Alternatives considered**: timestamp suffixes (rejected — every re-run produces a new file, which is precisely the duplicate-ingestion failure FR-014 names) and bare original filenames (rejected — collisions between different documents silently overwrite).

---

## R9. Access model

**Decision**: No authentication on the web service, per the resolved clarification (FR-024). `DOCLING_SERVE_API_KEY` is still set on the engine.

**Rationale**: The engine key costs nothing and is not user-facing. It ensures that if the engine's port is ever accidentally published — a one-line mistake in the compose file — the engine is not immediately open to the LAN. It is defense against operator error, not a user-facing credential, and does not contradict the credential-free access the spec requires.

---

## Open items carried into implementation

| # | Item | Handling |
|---|---|---|
| O1 | Exact health endpoint path on `docling-serve` (assumed `/health`) | Confirm against `/docs` on first run; the web service's readiness check depends on it |
| O2 | Exact `docling-serve-cpu` tag to pin (README shows `v1.18.0` in examples) | Pin whatever tag is pulled and verified during the air-gap build; never `latest` |
| O3 | Behavior of `pull_policy: never` through the Portainer UI specifically, as opposed to the compose CLI validated here | Verify during first Portainer deploy; fallback is Portainer's "re-pull image" toggle left off |
| O4 | Whether `partial_success` from the engine should count as converted | Decide during implementation; current intent is to surface it distinctly on the page rather than silently treat it as success |
