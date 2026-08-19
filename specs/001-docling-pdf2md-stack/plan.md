# Implementation Plan: Offline Docling PDF-to-Markdown Stack

**Branch**: `001-docling-pdf2md-stack` | **Date**: 2026-08-18, revised 2026-08-19 (GitHub deployment; automatic splitting) | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-docling-pdf2md-stack/spec.md`

## Summary

Deliver a two-service Docker stack, deployed by Portainer straight from this GitHub repository onto the Mac mini, that turns complex PDFs into AnythingLLM-ready Markdown without the running services ever touching the internet.

The restriction is on the tool, not on the act of deploying it (spec Clarifications, 2026-08-19). Portainer clones the repository and the host's daemon pulls both images from a registry; from the moment the containers start, neither has a route out. Deployment therefore costs no credential and no hand-carried archive, and isolation costs nothing in convenience.

The upstream `docling-serve-cpu` image is the conversion engine — it already ships its models baked in and provides a queue, per-document timeouts, and an async task API. A small custom web service in front of it owns exactly what the spec asks for and upstream does not provide: a dependency-free browser page for uploads and live status, a durable job history, content-addressed output naming, and writing finished Markdown into a bind-mounted outbox the operator imports into AnythingLLM by hand.

Isolation is enforced by network topology rather than by promise. The engine sits alone on an `internal` network with no default route. The web service bridges that network and a masquerade-disabled bridge, which — as measured on the target host during planning — accepts LAN traffic on its published port while its own egress blackholes.

## Technical Context

**Language/Version**: Python 3.12 (web service); vanilla HTML/CSS/ES2022 for the browser page, no build step

**Primary Dependencies**: `docling-serve-cpu` (upstream container image, pinned by tag and digest, arm64, models baked in); FastAPI; `httpx`; `python-multipart`; `pypdf` (page counts and page-range extraction — pure Python, no system libraries, research.md R11); stdlib `sqlite3`. Delivery depends on GitHub for the stack definition and GHCR for both images, at deploy time only

**Storage**: SQLite job registry on a **named volume** (never a macOS bind mount — SQLite locking is unreliable across the VM filesystem bridge); PDFs in an inbox named volume; Markdown in a **bind-mounted outbox** on the Mac mini so the operator can reach it from Finder

**Testing**: `pytest`, `pytest-asyncio`, `httpx` ASGI transport; a stub `docling-serve` fixture for contract and integration tests; host-side scripts that verify the isolation requirements against the deployed stack

**Target Platform**: `linux/arm64` containers on an Apple M4 Mac mini, orchestrated by Portainer. Measured on the host: OrbStack 29.4.0, 10 CPUs and 8.38 GB available to the container VM. CPU-only — no GPU or MPS is reachable from Linux containers on macOS.

**Project Type**: Web service plus deployment artifacts — a single Python project serving its own static frontend, packaged as one image and composed with one upstream image

**Performance Goals**: 20-page text PDF converted in under 3 minutes (SC-003); page status reflects reality within ~2s of a change (FR-010); stack healthy within 5 minutes of host boot (SC-007)

**Constraints**: Zero internet egress for the running containers, enforced by network topology (FR-021); LAN-only reachability (FR-023); no credentials of any kind for users (FR-024) and none for deployment either (FR-031); page assets fully self-hosted (FR-025); engine memory capped so Portainer and other stacks stay responsive (SC-011); ~4.4 GB engine image pulled once on first deploy; redeploys must not change the running version by themselves (FR-032)

**Scale/Scope**: Small workgroup — tens of documents per day, batches up to 50 (SC-008), a handful of concurrent users, 2 conversion workers. A single document may be up to `MAX_TOTAL_PAGES` (10 000) and is converted as up to 100 parts, two in flight at a time

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status of the constitution**: `.specify/memory/constitution.md` is still the unmodified Spec Kit template — every principle is an unfilled `[PRINCIPLE_N_NAME]` placeholder. There are no ratified project principles to check this design against, so **no constitutional gate can pass or fail on its own terms**. This is recorded rather than glossed over: if principles are ratified later, this plan should be re-checked against them.

In their absence, the following gates are derived directly from the spec's own hard constraints and are enforced here as if they were principles.

| Gate | Source | Initial check | Post-design re-check |
|---|---|---|---|
| **Offline by construction** — isolation must be a structural property of the stack definition, not an operational promise | FR-021, FR-022 | PASS — engine on an `internal` network with no default route; models baked into the image | PASS — no design element requires egress; verification script included (FR-026). `pypdf` is installed at image build time like every other dependency and reads bytes already on disk, so splitting adds no runtime fetch of any kind |
| **LAN-only** — no interface published beyond the local network | FR-023 | PASS — one published port, bound on the host; no port forwarding in scope | PASS — engine publishes nothing; only the web port is exposed |
| **Single-definition deploy** — an operator deploys and redeploys entirely from Portainer | FR-015, FR-030 | PASS — one compose file, read by Portainer from the GitHub repository; host-side work is confined to creating the outbox directory | PASS — all isolation expressed in compose `networks`, nothing in host firewall rules, nothing hand-loaded |
| **Credential-free** — nothing that can expire, leak, or be lost | FR-024, FR-031 | PASS — no user accounts; public repository and public image package, so Portainer stores no token | PASS — the only secret in the deployment is the engine API key, generated by the operator as a stack variable and never shown to a user (research.md R9) |
| **Deliberate version changes** — the converting version changes only when someone decides it should | FR-032 | PASS — images pinned by tag and digest; GitOps polling and webhooks both off | PASS — webhook would also require inbound exposure of Portainer, contradicting FR-023 (research.md R5) |
| **Durability across redeploy** — outputs and history survive stop/redeploy | FR-017 | PASS — named volume for the registry, bind mount for the outbox | PASS — no state in container layers |
| **Simplicity** — no component without a requirement forcing it | Spec scale assumptions | PASS — two services | PASS — Redis/RQ, Postgres, and a frontend build toolchain were each considered and rejected; see [research.md](./research.md) R2, R7. Splitting adds one pure-Python dependency and one table, not a component |
| **Never silently lose content** — output is either complete, or visibly not | FR-029, FR-035 | PASS — suspect yield is reported, never hidden | PASS — a document whose parts partly failed is written and marked incomplete in the file itself, and overlap-and-deduplicate was rejected precisely because it can delete a page silently (research.md R15) |
| **Self-hosted assets** — the page works for a client with no internet | FR-025 | PASS — no CDN, no web fonts, no build step | PASS — vanilla frontend, assets served from the image |

**Result**: No violations to justify. The Complexity Tracking section is therefore omitted.

## Project Structure

### Documentation (this feature)

```text
specs/001-docling-pdf2md-stack/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── web-api.md           # Browser-facing HTTP contract
│   ├── docling-serve.md     # Upstream engine contract we consume
│   └── stack.md             # Compose/Portainer deployment contract
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/pdf2md/
├── __init__.py
├── main.py                  # FastAPI app factory, lifespan, static mount
├── config.py                # Settings from environment, with defaults
├── db.py                    # SQLite schema, migrations, job queries
├── models.py                # Pydantic models for jobs, batches, API payloads
├── naming.py                # slug + content-hash output naming (research.md R8)
├── pdfinfo.py               # page count + page-range extraction via pypdf (research.md R11)
├── sectioning.py            # split joined Markdown into section files (research.md R13)
├── storage.py               # inbox/outbox filesystem operations
├── docling_client.py        # async client for the engine's v1 task API
├── dispatcher.py            # submit queued jobs and their parts, poll, fetch-and-persist once
├── api/
│   ├── __init__.py
│   ├── uploads.py           # POST /api/uploads
│   ├── jobs.py              # GET /api/jobs, /api/jobs/{id}, download
│   └── health.py            # GET /api/health, /healthz
└── static/
    ├── index.html           # the single operations page
    ├── app.js               # upload, poll, render — no framework, no CDN
    └── styles.css

tests/
├── conftest.py              # app fixture, temp volumes, stub engine
├── contract/                # web API shape; engine client vs documented contract
├── integration/             # upload → convert → outbox → download, restart recovery
└── unit/                    # naming, config, db queries

deploy/
├── docker-compose.yml       # the Portainer stack definition
├── .env.example             # every tunable, with the values this plan chose
└── README.md                # deploy and redeploy steps for the operator

ops/
├── verify-engine-image.sh   # confirm pinned digest and that models are baked in (research.md R4)
├── verify-offline.sh        # FR-026: prove no egress from either service
└── verify-lan-only.sh       # FR-026: prove reachable on LAN, engine unpublished

.github/workflows/
├── ci.yml                   # ruff + pytest on every push
└── publish.yml              # build web image on ubuntu-24.04-arm, push to GHCR on a v* tag

Dockerfile                   # web service image (python:3.12-slim, arm64)
pyproject.toml
```

**Structure Decision**: A single Python project at `src/pdf2md/` serves both the JSON API and its own static page, so there is no separate frontend project and no Node toolchain in the build. `deploy/` holds the artifact the operator actually consumes — the compose file Portainer reads from this repository at `deploy/docker-compose.yml`, which is why that path is part of the deployment contract and cannot be moved casually. `ops/` holds the verification scripts, deliberately outside the image because they run on the host against a deployed stack. `.github/workflows/` holds the build that produces the web image; it is the only place in the repository that needs credentials, and they never leave the workflow run.

## Phase 1 Design Artifacts

| Artifact | Covers |
|---|---|
| [data-model.md](./data-model.md) | `Batch`, `SourceDocument`, `ConversionJob`, `MarkdownOutput`; SQLite schema; job state machine and its recovery paths |
| [contracts/web-api.md](./contracts/web-api.md) | The browser-facing endpoints backing FR-008 through FR-014 |
| [contracts/docling-serve.md](./contracts/docling-serve.md) | The upstream async task API we depend on, including the single-use-result hazard |
| [contracts/stack.md](./contracts/stack.md) | Services, networks, volumes, ports, and every environment variable with its chosen value, including the six splitting knobs |
| [quickstart.md](./quickstart.md) | Deploying from GitHub through Portainer, and the runnable checks that validate each spec requirement |

## Risks

| Risk | Impact | Handling |
|---|---|---|
| Engine memory exhausts the 8.38 GB container VM under a 50-document batch | Host becomes unresponsive; SC-011 fails | `mem_limit` on the engine, 2 workers, `SHARE_MODELS=true`; measure RSS during implementation (research.md R6) |
| Single-use results: a crash between fetching and persisting loses a conversion | A document silently missing from the outbox | Fetch and persist in one transaction; mark the job failed on any error so it is visible and re-runnable, never silently dropped (research.md R3) |
| Portainer's Repository method behaves differently than the compose CLI validated here — compose path, stack variables, or digest reuse | First deploy fails, or a redeploy re-downloads 4.4 GB | Verify on the first Git-method deploy (research.md O5); the compose file is unchanged by Portainer, so a failure is diagnosable from the repo copy |
| A future image tag ships without baked models (upstream teased `docling-serve-slim`) | The stack deploys, reports healthy, and fails on the first scanned page | `ops/verify-engine-image.sh` checks the pinned digest and that the artifacts directory is populated, run against the pulled image (research.md R4) |
| The GHCR package is created private by the first CI publish | Deploy fails on the Mac mini with an authentication error, and FR-031 is silently violated if someone fixes it by storing a token | Pull anonymously from a machine that has never authenticated, before the first real deploy (research.md O6) |
| GHCR or GitHub is unreachable when a redeploy is attempted | The operator cannot redeploy, though the running stack is unaffected | `pull_policy: missing` means an unchanged digest is served locally; only a version change actually needs the network, and version changes are already deliberate (FR-032) |
| Masquerade-disabled bridge still permits traffic to the Docker host itself | Narrow egress path from the web service | Engine — the only component that would attempt downloads — is on the fully internal network; behavioral verification covers the rest (research.md R1) |
| The per-part watchdog is misconfigured back to per-document | Every split document dies at 45 minutes, after burning the engine time | The watchdog's shape is part of the state machine, not a setting: `timed_out` is defined per part in [data-model.md](./data-model.md), and a split document's expected runtime already exceeds any single part's allowance (research.md R12) |
| A table spanning a part boundary is broken in the output | Table fidelity falls below SC-002 | Bounded by arithmetic — 19 seams in a 2000-page document against hundreds of tables — and gated by SC-013, which measures seam tables separately (research.md R15) |
| Section files from a previous engine version linger in the outbox | AnythingLLM cites stale content that no longer matches the source | Re-conversion deletes that document's previous section files first, from the rows that name them — the only outbox deletion the service performs (research.md R13) |
| `pypdf` fails to parse a PDF the engine could have handled | A convertible document is refused at upload | Parse failure at upload maps to the existing "looks damaged" message and the document can still be retried; the same parse is what makes immediate password and page-count feedback possible (research.md R11) |
