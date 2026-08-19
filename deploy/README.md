# Deploying the PDF→Markdown stack on the Mac mini

Two containers, one stack definition, no internet.

| Service | What it does |
|---|---|
| `web` | The page people open, the job registry, and the writer of the outbox folder |
| `docling` | The conversion engine. Reachable only from `web`; publishes nothing |

Everything below is done from Portainer, except the one-time preparation in step 1 —
creating the outbox directory and loading the two images. After that, every deploy,
redeploy, stop, and start happens in the Portainer UI with no host shell.

Deploying it for the first time, or redeploying it? Work through
[`PORTAINER-EE-CHECKLIST.md`](./PORTAINER-EE-CHECKLIST.md) with this document open
beside it — the checklist is the same procedure as a set of boxes to tick, including the
Portainer EE options whose defaults are wrong for an air-gapped host. Read the sections
below for why any of it is the way it is.

---

## 1. One-time preparation (on the Mac mini)

**Create the outbox directory.** This is the folder you will open in Finder and import
into AnythingLLM from:

```bash
mkdir -p ~/pdf2md-outbox
```

**Load the images.** They are transferred from a connected machine as archives, because
this host has no registry access:

```bash
# on a connected machine, from the repository root
./ops/save-images.sh            # writes ./dist with both archives and SHA256SUMS

# move ./dist to the Mac mini however you like (USB, LAN copy), then here:
./ops/load-images.sh /path/to/dist
```

`save-images.sh` refuses to export an engine image whose model directory is empty. That
check is the difference between a stack that converts documents offline and one that
fails on its first conversion trying to download weights.

Expect roughly 2 GB of compressed archives and about 8 GB of disk once loaded.

---

## 2. Deploy in Portainer

Step by step, with every box to tick: [`PORTAINER-EE-CHECKLIST.md`](./PORTAINER-EE-CHECKLIST.md).
The short version:

1. **Stacks → Add stack**, name it `pdf2md`.
2. Paste the contents of [`docker-compose.yml`](./docker-compose.yml).
3. Add the environment variables. The two required ones:

   | Variable | Value |
   |---|---|
   | `PDF2MD_ENGINE_API_KEY` | any long random string — `openssl rand -hex 32` |
   | `OUTBOX_HOST_PATH` | the directory from step 1, e.g. `/Users/you/pdf2md-outbox` |

   Everything else has a working default; see [`.env.example`](./.env.example) for the
   full list with the reasoning behind each value.

4. **Leave the "re-pull image" toggle OFF.** ⚠️ This stack has no internet access. If
   Portainer tries to re-pull, the deploy fails — it does not fall back to the local
   image. The compose file sets `pull_policy: never` for the same reason.

5. Deploy.

The engine warms its models at startup, so it takes a few minutes to report healthy on
the first run. `web` waits for it (`depends_on: service_healthy`), so the page does not
come up against a cold engine.

**Check it worked:**

```bash
curl -s http://<mac-mini-ip>:8080/api/health
```

`"status": "ok"` with `engine.reachable: true` and `outbox.writable: true` means the
stack is ready. Open `http://<mac-mini-ip>:8080` from any machine on the LAN.

---

## 3. Redeploy, stop, start

All from Portainer → Stacks → `pdf2md`:

- **Redeploy** after changing a variable or shipping a new web image: *Editor* → *Update
  the stack*, with the re-pull toggle still off.
- **Stop / Start** from the stack's controls.
- Both services carry `restart: unless-stopped`, so they come back on their own after a
  Mac mini reboot with nothing for you to do (FR-016).

Job history and converted documents survive all of this — they live on volumes and in
the outbox directory, never in the container layers (FR-017).

---

## 4. Storage: what lives where, and how it grows

| Location | Type | Contents | Growth | Managed by |
|---|---|---|---|---|
| `db` volume → `/data/db` | Named volume | `pdf2md.sqlite` + WAL files, the job registry | A few KB per job; history older than `PDF2MD_JOB_HISTORY_DAYS` (30) is pruned automatically | The service |
| `inbox` volume → `/data/inbox` | Named volume | Uploaded PDFs, named by content hash | The size of what people upload; deleted 48 h after a job succeeds, or 14 days after it fails so a retry stays possible | The service |
| `$OUTBOX_HOST_PATH` → `/data/outbox` | **Bind mount** | The converted `.md` files — the durable record and the AnythingLLM handoff | Roughly 1–5% of the source PDF size per document, and **never deleted automatically** | **You** |

Two consequences worth internalizing:

- **The outbox is yours to manage.** Nothing prunes it. It is the record that survives
  everything else, which is exactly why history pruning never touches it.
- **The database is on a named volume on purpose.** SQLite's locking is unreliable
  across the macOS-to-VM filesystem bridge, so moving it to a bind mount to "make it
  easier to back up" will corrupt it. Back it up by copying it out of the volume
  instead.

Plan for around 8 GB for the images plus whatever the outbox accumulates.

---

## 5. Health and logs

- **Portainer's health column** reflects real checks: `/healthz` on the web service and
  the engine's own health endpoint (FR-018).
- **`GET /api/health`** is the detailed view: engine reachability, backlog depth, outbox
  writability and free space, and how many documents are in the outbox.
- **Logs** (Portainer → Containers → `web` → Logs) carry one line per job state change,
  each with the job id, the source filename, and the outcome — enough to diagnose a
  failure without any other tool (FR-019). Log rotation is configured at 10 MB × 5 files
  per service.

A degraded health status with `engine.reachable: false` still accepts uploads: they
queue and convert when the engine returns.

---

## 6. How the isolation works, and how to prove it

Isolation here is a property of the stack file, not a promise or a host firewall rule.
That matters because a Portainer redeploy carries the stack file with it and would not
carry anything you configured outside it.

### The topology

```
        LAN ──▶ :8080 ──▶ ┌─────┐        ┌─────────┐
                          │ web │──core──│ docling │
                          └─────┘        └─────────┘
                             │                │
                          (no SNAT)      (no route)
                             ✗                ✗
                          internet         internet
```

- **`core` is `internal: true`.** It has no default route, so the engine — the only
  component that would ever try to download a model — cannot reach anything outside the
  stack. Attempts fail immediately with "Network unreachable".
- **`edge` is a bridge with `com.docker.network.bridge.enable_ip_masquerade: "false"`.**
  The published port still accepts LAN traffic, while the web service's own outbound
  traffic has no SNAT return path and blackholes.
- **The engine publishes nothing.** It is reachable at `docling:5001` from the web
  service and from nowhere else.
- **The web service refuses to start** if `PDF2MD_ENGINE_URL` resolves to a public
  address — a misconfiguration that no network topology could undo.

⚠️ **Do not consolidate onto a single `internal` network.** It looks tidier and it was
measured on this host: published ports stop working entirely and the page becomes
unreachable from the LAN. The two-network split is what makes both properties hold at
once (research.md R1).

### Proving it

Two scripts, both safe to run against a live stack:

```bash
./ops/verify-offline.sh              # egress blocked both ways, engine still reachable
./ops/verify-lan-only.sh 10.0.0.19   # page answers on the LAN, engine publishes nothing
```

`verify-offline.sh` checks that a TCP connection from each container to a public address
fails, that `web → docling:5001` succeeds, that the engine publishes no ports, and that
the engine log contains no download attempts.

`verify-lan-only.sh` checks the LAN address answers, that only `web` publishes a port,
and that the API needs no credential. It then asks you to confirm the three things a
script running on this host cannot see: that the address is unreachable from outside the
network, that no router port-forward or reverse proxy maps it, and that no tunnelling
service is exposing it.

**Run both after any change to `networks:`, `ports:`, or the engine's environment.**

### Why there are no passwords

Presence on the local network is the authorization (FR-024). There is no login, no
shared credential, and nothing for anyone to lose or leak. `PDF2MD_ENGINE_API_KEY` is
not an exception: no user is ever asked for it. It is set on the engine so that if the
engine's port were ever published by a one-line mistake in this file, the engine would
not be immediately open to the network.

---

## 7. Importing into AnythingLLM

Delivery into AnythingLLM is deliberately manual — the stack writes files, you decide
when to ingest them.

1. Open the outbox folder (`$OUTBOX_HOST_PATH`, e.g. `~/pdf2md-outbox`) in Finder.
2. Import its contents into your AnythingLLM workspace as you would any other documents.
3. Nothing needs deleting afterwards. Leave the files there; they are the durable record.

### What the filenames mean

```
annual-report-2026--4f2a91b0c7d3.md
└────── slug ──────┘  └─ hash ─┘
```

- The **slug** comes from the original PDF filename, so a human can recognize it.
- The **hash** is the first 12 characters of the SHA-256 of the PDF's bytes, so it is
  the document's identity, not its name.

Two consequences you can rely on when importing:

- **Re-converting a document never creates a second file to ingest.** The same bytes
  always produce the same filename, so a re-conversion overwrites in place. Uploading a
  document that has already been converted does not even reach the engine — the page
  reports *Already converted* and points at the file that already exists.
- **A renamed copy is the same document.** `report.pdf` and `report (copy).pdf` with
  identical contents resolve to one output file, so AnythingLLM never ingests the same
  content twice under two identities. Conversely, two genuinely different documents that
  happen to share a filename get different files.

### Before you import

Look at the page's list once. Two states deserve a glance before ingesting:

- **"Converted — check output"** means the Markdown came out implausibly small for the
  document — often a blank scan, sometimes text that was not recognized. The file is
  there and downloadable; open it rather than importing it blind.
- **"Converted"** with a note about parts that could not be fully read means the engine
  reported a partial success. Usually a table or two; worth a look.

Anything that failed produced no file at all, so nothing broken can reach AnythingLLM by
accident.

---

## 8. Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Deploy fails trying to pull an image | The re-pull toggle was left on, or a tag is missing locally | `docker images`; re-run `ops/load-images.sh`; redeploy with the toggle off |
| Engine never reaches healthy | Models missing from the image, or the warm-up is slower than the start period | Engine logs for a download attempt; re-run `ops/save-images.sh`, whose model check would have caught it |
| Conversions work but the page is unreachable from the LAN | The networks were consolidated onto `internal` — published ports do not work there | `docker network inspect`; see research.md R1 |
| Host becomes sluggish under a batch | Engine memory or thread count too high | Lower `DOCLING_WORKERS`, keep `SHARE_MODELS` true, tighten `DOCLING_MEM_LIMIT` |
| A job sits at Converting forever | The watchdog is above the engine's own timeout | `PDF2MD_JOB_TIMEOUT_SECONDS` must stay above `DOCLING_MAX_DOCUMENT_TIMEOUT`, and both must be finite |
| Database errors after a redeploy | SQLite was moved onto a bind mount | It belongs on the named volume |

---

## 9. Version pinning, and what has been verified

The engine image is pinned to an exact tag and its `linux/arm64` digest:

```
ghcr.io/docling-project/docling-serve-cpu:v1.18.0
sha256:6aa1b1428b5c83db2a4fc3431d99902ef115d9e1ce13eed0f716d23ed9d9a098
```

Verified against that tag before deployment:

| Claim | How it was checked |
|---|---|
| The tag exists for `linux/arm64` | Registry manifest list |
| The image expects models at `/opt/app-root/src/.cache/docling/models` | `DOCLING_SERVE_ARTIFACTS_PATH` in the image's own environment |
| Tesseract data ships with it | `TESSDATA_PREFIX` in the image's environment |
| `/ready` gates on model loading; `/health` only reports the process is up; neither needs the API key | The pinned tag's source |
| `/v1/convert/file/async`, `/v1/status/poll/{id}`, `/v1/result/{id}` exist, and `X-Api-Key` is the header name | The pinned tag's source |

**Still to verify on the Mac mini itself**, because they cannot be checked from a
registry — each is a step in the runbook rather than an open question:

- The model directory is actually populated. `ops/save-images.sh` checks this at export
  time and aborts if it is not.
- `pull_policy: never` behaves through the Portainer UI as it does through the compose
  CLI. Confirm on the first deploy; the fallback is the re-pull toggle staying off.

**Upgrading the engine is a deliberate act**, never a re-pull: pull and verify on a
connected machine, re-run the air-gap transfer, redeploy, then re-run
`ops/measure-fidelity.py`. An upgrade that quietly changes layout analysis shows up
there and nowhere else.

---

## 10. Measurements to record here

These come from the deployed stack and belong in this file once run, so the next person
inherits numbers rather than assumptions.

| What | How | Result |
|---|---|---|
| Engine memory under a 50-document batch, and the `DOCLING_MEM_LIMIT` it implies | `docker stats` during quickstart V9 | _not yet measured — current limit 5g is a starting value_ |
| 20-page text PDF conversion time (SC-003: under 3 minutes) | Convert one and time it | _not yet measured_ |
| Clean-host deploy time from this document alone (SC-004: under 30 minutes) | Time a deploy on a fresh host | _not yet measured_ |
| Fidelity against the corpus (SC-001, SC-002, FR-004) | `python3 ops/measure-fidelity.py --base-url http://<ip>:8080` | _not yet measured — corpus not assembled_ |
| AnythingLLM spot checks (SC-009: ≥9 of 10 cite the right document) | Import the outbox, ask 10 questions | _not yet measured_ |
