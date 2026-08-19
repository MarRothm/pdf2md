# Phase 0 Research: Offline Docling PDF-to-Markdown Stack

**Feature**: `001-docling-pdf2md-stack` | **Date**: 2026-08-18, revised 2026-08-19 | **Plan**: [plan.md](./plan.md)

> **Revised 2026-08-19** after the clarification that deployment happens over the internet from GitHub while the *running stack* stays sealed. R5 is replaced, R4's rationale is rewritten, and R10 is new. R1, R2, R3, R6, R7, R8, and R9 are unaffected — the isolation topology they describe is exactly what still has to hold.

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

**Rationale**: The models are already inside the published image, which is exactly what FR-022 asks for — presence of the models is a property of the artifact, so it survives a redeploy, a deleted volume, and a fresh host. Keeping the image stock also means the engine is pulled from its own upstream registry rather than rebuilt or repackaged by us, so there is nothing to keep in sync with an upstream release.

**OCR engine**: Take the image default. Upstream's `docling-tools models download` output shows RapidOCR weights (torch and onnxruntime, English and Chinese) fetched as part of the default set, so scanned-page recognition works offline with no extra assets. EasyOCR language packs are the exception — they are downloaded only on explicit request (`docling-tools models download easyocr --easyocr-lang ...`) and so are unavailable unless baked in. This matches the spec's English-only assumption.

**VERIFY AT IMPLEMENTATION**: confirm the pinned tag actually contains a populated artifacts directory —
`docker run --rm --entrypoint sh <image> -c 'ls /opt/app-root/src/.cache/docling/models'`. Upstream has teased `docling-serve-slim` images that *skip* model weights; those must never be used here. This check used to run at export time in `ops/save-images.sh`; with that script retired it moves to `ops/verify-engine-image.sh`, run on the Mac mini against the pulled image (R5).

**Alternatives considered**:

- *Mounting a host directory of models populated by `docling-tools models download` on a connected machine*: rejected as redundant given the image already carries them, and it adds a second artifact to keep in sync with the image version.
- *Serving models from the Mac mini's host Ollama instance*: rejected, and worth recording because the hardware argument for it is real — containers on macOS cannot reach Metal, so the engine is CPU-only permanently, while host Ollama is not. It does not work: docling's layout, table-structure, and text-recognition models are not language models and have no Ollama representation. The only thing Ollama could serve is docling's alternative `VlmPipeline`, which replaces the whole extraction cascade with a vision model that *generates* the Markdown. For a corpus destined for retrieval that inverts the failure mode — a cascade that mis-parses a table produces a visibly broken table, while a VLM produces a plausible wrong one that gets cited as fact. Docling's own documentation publishes inference times for that pipeline but no accuracy figures, and its per-page timings (~6s at the fastest) put a 20-page document near SC-003's whole-document budget on inference alone. It would also require giving the engine a route to the host and enabling `DOCLING_SERVE_ENABLE_REMOTE_SERVICES`, which permits any URL, not just the local one. Ollama-served figure captioning remains viable as a separate opt-in feature (spec Assumptions).

---

## R5. Deployment delivery: GitHub as the source

**Decision**: Portainer deploys the stack from the GitHub repository using its **Repository** build method — Repository URL, Repository reference `main`, Compose path `deploy/docker-compose.yml`, Authentication toggle **off** — and the host's Docker daemon pulls both images from GHCR. `pull_policy: never` is replaced by `pull_policy: missing`, both images are pinned by tag **and** digest, and **GitOps updates stay off**.

**Rationale**:

- **The deployed definition is the repository's** (FR-030). Portainer clones the repo at deploy time, so what runs on the Mac mini and what is in version control cannot drift the way a pasted editor buffer can. The compose file is left unmodified by Portainer, which is what allows the environment variables to stay as `${...}` placeholders resolved from stack variables.
- **No credential anywhere** (FR-031). A public repository needs no Authentication toggle, and a public GHCR package pulls anonymously. Nothing on the Mac mini or in Portainer expires, so no unattended redeploy can fail on a stale token months from now.
- **Digest pinning, not tag trust** (FR-032). A tag can be re-pointed upstream; `image: name:tag@sha256:...` cannot. This is what makes "pinned to an exact version" a fact about the bytes rather than about a label.
- **`pull_policy: missing`** is the right partner to digest pinning: the daemon reuses the local image when the digest is already present, so a redeploy does not re-download 4.4 GB, but a fresh host pulls what it needs without hand-loading.
- **GitOps updates off** (FR-032). Both mechanisms Portainer offers are rejected for different reasons. *Polling* would change the version doing the converting without anyone deciding to, potentially mid-batch, and an engine upgrade quietly changes layout analysis — the kind of change that surfaces as degraded retrieval weeks later, nowhere near the deploy. *Webhook* is worse: GitHub's runners would have to reach Portainer, which means exposing it inbound and contradicting FR-023 directly.

**What this removes**: `ops/save-images.sh`, `ops/load-images.sh`, the `SHA256SUMS`/`IMAGES` transfer artifacts, and the whole "leave Portainer's re-pull toggle off" caveat — *Re-pull image* is an option of the GitOps update mechanism, so with GitOps off it does not apply at all.

**What it preserves**: the model-population check that `save-images.sh` performed at export time still matters — a slim tag would deploy cleanly, report healthy, and fail on the first scanned page. It moves to `ops/verify-engine-image.sh`, run on the Mac mini against the pulled image, checking both the digest and that the artifacts directory is populated.

**First-deploy cost**: ~4.4 GB engine plus ~200 MB web, pulled once over the operator's connection. SC-004's 30-minute budget explicitly excludes this download.

**Alternatives considered**:

- *Keeping the air-gap transfer as the primary path*: superseded by the clarification. The restriction was always about where documents go, not about how bytes reach the host.
- *Keeping `save-images.sh`/`load-images.sh` as a documented fallback*: rejected. Two supported paths means two paths to keep correct, and a fallback is exercised only in an emergency — which is exactly when an unexercised path turns out to be broken. Their history remains in git if a genuinely disconnected host ever appears.
- *`pull_policy: always`*: rejected. With digests pinned it buys nothing, re-downloads on every redeploy, and converts a transient GHCR outage into a failed redeploy of a stack that was working a minute earlier.
- *Portainer BE relative path volumes*: not needed. The only host path is the outbox, supplied as an absolute path in a stack variable.

**VERIFY AT IMPLEMENTATION**: the first Repository-method deploy through Portainer EE — that the compose path resolves, stack variables are applied to the `${...}` placeholders, no credential prompt appears for the public repo, and a redeploy reuses the already-pulled digests rather than re-downloading.

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
- FR-025 requires the page to work for a client with no internet: no CDN, no web fonts, no external analytics. A no-build-step vanilla page makes that a property of the source tree rather than a bundler configuration to audit. It also keeps the image build to a single `pip install`, which is what makes a native arm64 CI build cheap (R10).
- SQLite covers the durable job registry (FR-017) with no additional container.

**Status transport**: the page polls `GET /api/jobs` on a short interval. Server-Sent Events were considered and rejected for v1 — polling at this scale is simpler and reconnects for free, and FR-010 requires only that status updates without user action.

**SQLite placement**: the database lives on a **named Docker volume**, never on a macOS bind mount. SQLite's locking is unreliable across the macOS-to-VM filesystem bridge. The outbox, which the operator needs to reach from Finder for the manual AnythingLLM import, is a bind mount — plain file writes there are safe.

**Alternatives considered**: Postgres (rejected — a third container and a daemon to operate for a single-writer workload of tens of rows per day); a React/Vite frontend (rejected — a build toolchain and a node_modules tree in the image build for one page).

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

## R10. Building and publishing the web image

**Decision**: GitHub Actions builds `pdf2md-web` natively on an `ubuntu-24.04-arm` runner and pushes it to `ghcr.io/<owner>/pdf2md-web`, tagged with the release version. The compose file references it by tag and digest. The workflow triggers on a release tag (`v*`), not on every push to `main`.

**Rationale**:

- **Native arm64, not emulation.** GitHub's hosted arm64 runners (`ubuntu-24.04-arm`, `ubuntu-22.04-arm`) are free for public repositories, which makes a native build of an arm64-only image free and fast. Cross-building under QEMU is slow for a pip-install-heavy image and has no upside here. Note that those runner labels **do not work in private repositories** — the public-repository clarification (FR-031) is therefore load-bearing for the build pipeline too, not only for credential handling.
- **Release-tag trigger, not push-to-main.** FR-032 asks that the deployed version change only when someone decides it should. Publishing an image per commit invites a moving reference and makes "which build is on the Mac mini" a question about timing rather than about a version number.
- **`GITHUB_TOKEN` with `packages: write`** is the only credential in the pipeline, and it lives in the workflow run rather than on the host.

**Alternatives considered**: `docker buildx` with QEMU on an x86 runner (rejected — slow, and unnecessary now that native runners are free); building on the Mac mini itself (rejected — reintroduces exactly the host-side tooling FR-015 and FR-030 remove); a `latest` tag (rejected — FR-032 requires an exact pin).

**VERIFY AT IMPLEMENTATION**: that the published package is pullable anonymously from a machine that has never authenticated to GHCR, and that the digest recorded in the compose file matches the pushed image.

---

## Open items carried into implementation

| # | Item | Handling |
|---|---|---|
| O1 | Exact health endpoint path on `docling-serve` (assumed `/health`) | **RESOLVED during implementation.** The pinned tag exposes `/health` (process is up, no auth), `/ready` and `/readyz` (503 until the models are loaded), `/livez`, and `/version`. `/ready` is the correct gate: it is what the compose healthcheck and `PDF2MD_ENGINE_HEALTH_PATH` now use, so `web` never starts against a cold engine |
| O2 | Exact `docling-serve-cpu` tag to pin (README shows `v1.18.0` in examples) | **PINNED to `v1.18.0`** — `linux/arm64` digest `sha256:6aa1b1428b5c83db2a4fc3431d99902ef115d9e1ce13eed0f716d23ed9d9a098`. Confirmed from the registry that the tag exists for arm64 and that the image sets `DOCLING_SERVE_ARTIFACTS_PATH=/opt/app-root/src/.cache/docling/models` and `TESSDATA_PREFIX`. The models-populated check moved to `ops/verify-engine-image.sh`, run against the pulled image (R4, R5) |
| O3 | ~~Behavior of `pull_policy: never` through the Portainer UI~~ | **OBSOLETE.** Superseded by the GitHub deployment clarification — the stack now pulls from a registry (R5). Replaced by O5 |
| O5 | First deploy through Portainer EE's **Repository** method | Verify the compose path resolves, stack variables reach the `${...}` placeholders, no credential is requested for the public repo, and a redeploy reuses already-pulled digests (R5) |
| O6 | GHCR package visibility after the first CI publish | FR-031 requires anonymous pulls. Confirm by pulling from a machine that has never logged in to GHCR; if the package was created private, set it public once in the package settings (R10) |
| O4 | Whether `partial_success` from the engine should count as converted | **DECIDED: it counts as converted, and is surfaced distinctly.** The job ends `succeeded`, the Markdown is written, and `engine_status=partial_success` travels with the job so the page says parts could not be fully read. FR-029's suspect-yield check is separate: it ends a job `succeeded_suspect` when the output is implausibly small for the source |
