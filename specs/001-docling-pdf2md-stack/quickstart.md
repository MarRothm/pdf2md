# Quickstart & Validation Guide

**Feature**: `001-docling-pdf2md-stack` | **Plan**: [plan.md](./plan.md)

How to get the stack onto the Mac mini from GitHub, and the runnable checks that prove each spec requirement holds. Design detail lives in [contracts/stack.md](./contracts/stack.md) and [data-model.md](./data-model.md) — this document is the run guide.

Replace `10.0.0.19` with the Mac mini's LAN address throughout.

---

## Prerequisites

**On the Mac mini**
- Container runtime with Portainer EE already in use (measured on the target: OrbStack 29.4.0, `linux/arm64`, 10 CPUs / 8.38 GB to the VM, `portainer/portainer-ee` present)
- An outbox directory created and writable, e.g. `~/pdf2md-outbox`
- ~8 GB free disk for the pulled images, plus room for the outbox
- **Internet access for the deploy itself** — Portainer reaches GitHub, the daemon reaches `ghcr.io`. The running stack never uses it (FR-021)

**Nowhere else.** There is no second machine, no archive, and no credential.

---

## Step 1 — Publish the web image (once per version)

Push a release tag; CI builds natively on `arm64` and publishes to GHCR (research.md R10):

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Then confirm the package is pullable **without authentication** — from a machine that has never logged in to GHCR:

```bash
docker logout ghcr.io
docker pull ghcr.io/<owner>/pdf2md-web:1.0.0
docker inspect --format '{{index .RepoDigests 0}}' ghcr.io/<owner>/pdf2md-web:1.0.0
```

**Expected**: the pull succeeds anonymously. If it asks for credentials, the package was created private — fix its visibility in the package settings rather than storing a token (FR-031, research.md O6).

Record the printed digest in `deploy/docker-compose.yml`. Both images are pinned as `name:tag@sha256:...`; the tag is for humans and the digest is the actual pin.

## Step 2 — Prepare the Mac mini

The only host-side step, and the only shell command in the whole deployment:

```bash
mkdir -p ~/pdf2md-outbox
```

Create it before deploying. If the bind-mount source is missing at deploy time, Docker creates it root-owned and it cannot be opened in Finder.

## Step 3 — Deploy from GitHub in Portainer

**Stacks → Add stack → Repository**, then:

| Field | Value |
|---|---|
| Name | `pdf2md` |
| Repository URL | this repository's HTTPS URL |
| Authentication | **off** (public repository) |
| Repository reference | `refs/heads/main` |
| Compose path | `deploy/docker-compose.yml` |
| GitOps updates | **off** — no polling, no webhook (FR-032) |
| Environment variables | `PDF2MD_ENGINE_API_KEY` (`openssl rand -hex 32`), `OUTBOX_HOST_PATH` (absolute path from step 2) |

Deploy. The first deploy pulls ~4.4 GB of engine plus ~200 MB of web image, then the engine warms its models — `LOAD_MODELS_AT_BOOT=true` — so first start takes minutes. `web` waits on `depends_on: service_healthy` and never comes up against a cold engine.

```bash
curl -s http://10.0.0.19:8080/api/health | jq
```

**Expected**: `"status": "ok"`, `engine.reachable: true`, `outbox.writable: true`.

## Step 4 — Confirm the engine really carries its models

The failure this catches is a stack that deploys, reports healthy, and fails on the first scanned page:

```bash
./ops/verify-engine-image.sh
```

**Expected**: the running engine image matches the pinned digest, and `/opt/app-root/src/.cache/docling/models` is populated. A `-slim` variant would fail here (research.md R4).

---

## Validation scenarios

Each maps to spec requirements. Run them in order after a fresh deploy.

### V1 — Convert a complex PDF (User Story 1; FR-001…FR-005)

Use a PDF containing at least one multi-column page, one table, and one scanned page.

1. Open `http://10.0.0.19:8080` from another machine on the LAN.
2. Upload the PDF and watch it move Queued → Converting → Converted.
3. Download the Markdown from the page.

**Expected**: headings appear as Markdown headings; body text is in reading order across columns; the table is a Markdown table; text from the scanned page is present. The same file exists in the outbox under the same name.

### V2 — Reject bad input (FR-007)

Upload a `.txt` file, a zero-byte file, and a corrupt PDF in one batch alongside a valid one.

**Expected**: the valid document converts; each bad file is rejected or failed with a plain-language reason; **no `.md` appears in the outbox for any of them**.

### V3 — Live status without reloading (FR-010, FR-011)

Upload a large document and leave the page untouched.

**Expected**: status changes on its own within a couple of seconds of each transition; queue position shows while queued; a failure shows its reason without opening logs.

### V4 — Deterministic naming, no duplicates (FR-014)

Upload the same PDF twice, then upload a copy renamed to something else.

**Expected**: outbox filename is `{slug}--{hash12}.md`. The second upload reports **Already converted** and the outbox file count does not increase. The renamed copy also resolves to the same output — identical bytes, identical name — so AnythingLLM never ingests the same content twice under two identities.

### V5 — Offline: zero egress from the containers (FR-021, FR-022; SC-005)

The host pulled from the internet minutes ago; the point of this check is that the containers cannot, and never could.

```bash
./ops/verify-offline.sh
```

It must check, at minimum:

```bash
# Engine has no route off the internal network
docker compose exec docling sh -c 'wget -q -T 5 -O /dev/null http://1.1.1.1/ ; echo exit=$?'
# Web egress blackholes
docker compose exec web sh -c 'wget -q -T 5 -O /dev/null http://1.1.1.1/ ; echo exit=$?'
# Engine can still be reached by the web service over core
docker compose exec web sh -c 'wget -q -T 5 -O /dev/null http://docling:5001/health ; echo exit=$?'
```

**Expected**: both egress checks fail (the engine with "Network unreachable", the web service by timeout); the internal call succeeds. Then run a full conversion and confirm it succeeds with no download-related errors in the engine log.

> Validated during planning on this host: engine egress blocked, web egress blocked, `web → docling` reachable by service name, LAN inbound HTTP 200.

### V6 — LAN-only reachability (FR-023; SC-006)

```bash
./ops/verify-lan-only.sh
```

- From a LAN machine: `curl -s -o /dev/null -w '%{http_code}' http://10.0.0.19:8080/` → **200**
- `docker compose ps` → the `docling` service publishes **no** ports
- From outside the local network (e.g. a phone on cellular): the address is unreachable
- Confirm no port-forward or reverse proxy maps the port on the router

### V7 — Survives restart and redeploy (FR-016, FR-017; SC-007)

1. Note the outbox contents and the job list.
2. Redeploy the stack in Portainer, then reboot the Mac mini.

**Expected**: after boot, the stack returns to healthy unattended within 5 minutes; job history and outbox contents are intact; no manual step.

### V8 — Restart mid-batch (User Story 5 scenario 3)

Upload a batch of ~10 documents and restart the stack while conversions are in flight.

**Expected**: every job ends in a definite state. In-flight jobs whose PDFs are still in the inbox are resubmitted with `attempt=2`; any whose PDF is gone are marked failed with a reason naming the restart. **Nothing is left stuck at Converting.**

### V9 — Batch of 50 unattended (SC-008, SC-011)

Upload 50 mixed documents and leave it. While it runs, keep Portainer's UI open and use the Mac mini normally.

**Expected**: all 50 reach a terminal state; failures are individually reported and do not stall the rest; the Mac mini and Portainer stay responsive. Watch `docker stats` for engine memory against `mem_limit` — this run is what sets the final limit.

### V10 — Timeout behavior (FR-028)

Submit a pathological document, or temporarily lower `DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT` and `PDF2MD_JOB_TIMEOUT_SECONDS`.

**Expected**: the job reports Timed out with a reason; the queue keeps moving; no partial `.md` is written.

### V11 — Page works without internet (FR-025)

Open the page from a LAN client with its own internet access disabled, using a fresh browser profile so nothing is cached.

**Expected**: the page renders and functions fully. Check the browser network tab: **every request goes to the Mac mini's address** — no CDN, no font host, no analytics.

### V12 — Handoff into AnythingLLM (User Story 4; SC-009)

Copy the outbox contents into AnythingLLM and ingest them, then ask 10 spot-check questions.

**Expected**: at least 9 answers cite the correct source document. Confirm no duplicate documents appeared from re-converted files.

---

## Local development (no Mac mini needed)

```bash
uv sync                        # or: pip install -e '.[dev]'
pytest                         # unit + contract + integration against the stub engine
uvicorn pdf2md.main:app --reload --port 8080
```

The test suite runs against a stub `docling-serve` fixture, so no 4.4 GB image is needed to develop the web service. Only V1, V5, V6, V9, and V12 require the real stack.

The same suite runs in CI on every push; the image publish workflow runs only on a `v*` tag, so no ordinary commit changes what could be deployed (research.md R10).

---

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Deploy fails pulling an image | The GHCR package is private, or the pinned digest does not exist | Pull it by hand with `docker logout ghcr.io` first (step 1); check package visibility before considering a credential |
| Deploy fails resolving the stack file | Wrong compose path or reference for the Repository method | Compose path is `deploy/docker-compose.yml`, relative to the repository root (contracts/stack.md) |
| Engine unhealthy, log shows a download attempt | The image lacks baked models (a `-slim` variant?) | Step 4, `ops/verify-engine-image.sh` |
| Conversions succeed but the page is unreachable from the LAN | Everything landed on `internal` — published ports do not work there | `docker network inspect`; see [research.md](./research.md) R1 |
| Host becomes sluggish under a batch | Engine memory or thread count too high | Lower `ENG_LOC_NUM_WORKERS`, confirm `SHARE_MODELS=true`, tighten `mem_limit` |
| A job sits at Converting forever | Watchdog not firing, or the engine timeout is above ours | Both timeout values in [contracts/stack.md](./contracts/stack.md) |
| Database errors after a redeploy | SQLite was placed on a bind mount | It belongs on a named volume (research.md R7) |
