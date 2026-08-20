# Deploying the PDF→Markdown stack on the Mac mini

Two containers, one stack definition, deployed from GitHub. The stack itself has no
internet access — that restriction is about the documents, not about how the software
gets here.

| Service | What it does |
|---|---|
| `web` | The page people open, the job registry, and the writer of the outbox folder |
| `docling` | The conversion engine. Reachable only from `web`; publishes nothing |

Portainer reads the stack definition straight from this repository, and the host pulls
both images from GHCR. There is one host-side command in the whole procedure — creating
the outbox directory — and no credential anywhere.

Deploying for the first time, or redeploying? Work through
[`PORTAINER-EE-CHECKLIST.md`](./PORTAINER-EE-CHECKLIST.md) with this document open beside
it — the checklist is the same procedure as a set of boxes to tick. Read the sections
below for why any of it is the way it is.

---

## 1. One-time preparation (on the Mac mini)

**Create the outbox directory.** This is the folder you open in Finder and import into
AnythingLLM from:

```bash
mkdir -p ~/pdf2md-outbox
```

Create it *before* deploying. If the bind-mount source is missing at deploy time, Docker
creates it root-owned and you will not be able to open it in Finder.

Note its absolute path — `/Users/you/pdf2md-outbox`, not `~/pdf2md-outbox`. Portainer
does not expand `~`.

That is the entire host-side preparation. There is no archive to copy and no image to
load: the first deploy pulls ~4.4 GB of engine and ~200 MB of web image from GHCR. Budget
disk for both, plus room for the outbox.

---

## 2. Deploy in Portainer

Step by step, with every box to tick: [`PORTAINER-EE-CHECKLIST.md`](./PORTAINER-EE-CHECKLIST.md).
The short version:

1. **Stacks → Add stack**, name it `pdf2md`, build method **Repository**.
2. Fill in the repository fields:

   | Field | Value |
   |---|---|
   | Repository URL | `https://github.com/MarRothm/pdf2md` |
   | Authentication | **off** — the repository is public, so no credential is needed or wanted |
   | Repository reference | `refs/heads/main` |
   | Compose path | `deploy/docker-compose.yml` |

3. **Leave GitOps updates OFF.** ⚠️ Neither mechanism is appropriate here. *Polling*
   would change the version doing the converting without anyone deciding to, possibly
   mid-batch — and an engine upgrade quietly changes layout analysis, which surfaces as
   degraded retrieval weeks later, nowhere near the deploy. *Webhook* is worse: GitHub's
   runners would have to reach Portainer, which means exposing it inbound, and this stack
   publishes nothing to the internet.
4. Add the two environment variables. Everything else has a working default; see
   [`.env.example`](./.env.example) for the full list and the reasoning behind each value.

   | Variable | Value |
   |---|---|
   | `PDF2MD_ENGINE_API_KEY` | any long random string — `openssl rand -hex 32` |
   | `OUTBOX_HOST_PATH` | the directory from step 1, e.g. `/Users/you/pdf2md-outbox` |

5. Deploy. The first one pulls the images, then the engine warms its models, so it takes
   several minutes to report healthy. `web` waits on `depends_on: service_healthy`, so
   the page does not come up against a cold engine.

**Check that it worked:**

```bash
curl -s http://<mac-mini-ip>:8080/api/health
./ops/verify-engine-image.sh
```

`"status": "ok"` with `engine.reachable: true` and `outbox.writable: true` means the
stack is ready. `verify-engine-image.sh` confirms the running engine is the digest this
repository pins and that its models are baked in — the check that catches an engine
which deploys cleanly and then fails on the first scanned page.

Open `http://<mac-mini-ip>:8080` from any machine on the LAN.

---

## 3. Redeploy, stop, start

All from Portainer → Stacks → `pdf2md`:

- **Redeploy** after a change in the repository — including a release, which pins a new
  web image on `main`: *Pull and redeploy*. Because both images
  are pinned by digest and the compose file sets `pull_policy: missing`, an unchanged
  version costs no download — only an intentional version bump pulls anything.
- **Stop / Start** from the stack's controls.
- Both services carry `restart: unless-stopped`, so they come back on their own after a
  Mac mini reboot with nothing from you (FR-016).

Job history and converted documents survive all of it — they live on named volumes and in
the outbox directory, never in container layers (FR-017).

**Changing the deployed version is a deliberate act.** Edit the pinned digest in
`deploy/docker-compose.yml`, commit it, and redeploy. Nothing updates itself.

### Releasing a new version of the web service

Code on `main` is not deployable by itself. The stack pins an image by digest, so a change
only reaches the Mac mini once it has been built into an image and that image's digest has
been pinned. You do two things; the workflow does the rest.

```bash
# 1. Bump the version and push it. The version lives in exactly one place;
#    pyproject.toml reads it from there through hatchling.
sed -i '' 's/^__version__ = ".*"/__version__ = "1.3.0"/' src/pdf2md/__init__.py
git commit -am "Bump to 1.3.0" && git push

# 2. Tag it — this is what triggers .github/workflows/publish.yml
git tag v1.3.0 && git push origin v1.3.0
```

**3.** That is the last thing you type. The workflow builds the image, pushes it to GHCR,
writes the digest it just published into `deploy/docker-compose.yml`, and commits that pin
to `main` itself — so the digest is never transcribed by hand and the stack file always
points at the newest release. The job summary shows the full reference.

**4.** **Pull and redeploy** in Portainer. Portainer reads the stack file from `main`, so
the redeploy picks up the pin the workflow wrote.

Nothing deploys itself: GitOps updates stay off, and the redeploy is still a decision a
person makes. What the workflow removed is the transcription, not the decision.

Worth confirming after a release that the package stayed public — this is what a host with
no credential does:

```bash
docker logout ghcr.io && docker pull ghcr.io/marrothm/pdf2md-web:1.3.0
```

The digest lives in exactly one place. `deploy/.env.example` documents `WEB_IMAGE` and
`ENGINE_IMAGE` as deliberate overrides and deliberately carries no digest of its own; a
value there would be a stale second copy that silently outranks the stack file. A unit
test fails if one reappears.

Only the web image moves this way. The engine is upstream and pinned separately; upgrading
it is a bigger decision because it changes layout analysis (§10).

To see whether anything is waiting for a release: `git log --oneline $(git describe --tags --abbrev=0)..HEAD`

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

## 5. Long documents: splitting and section files

Two limits used to make a long PDF simply fail. Both are now handled without you doing
anything, and the third case — a document too long for any of it — is refused honestly.

| Document | What happens |
|---|---|
| Up to `PDF2MD_PART_MAX_PAGES` (100) | Converted whole, exactly as before |
| Longer than that | Split into page ranges, converted a couple at a time, and joined back into one document. The page shows *Converting — part 7 of 20* |
| Longer than `PDF2MD_MAX_TOTAL_PAGES` (10,000) | **Refused at upload**, in a second, for its length — with the suggestion to split it. Never described as damaged |
| Password-protected, or structurally unreadable | Also refused at upload now, rather than after a conversion attempt |

**If one part fails, the rest are still written.** The document reports *Converted — pages
N–M are missing*, and the gap is marked inside the Markdown as well as on the page. That
matters because job history is pruned after `PDF2MD_JOB_HISTORY_DAYS` while the file in
the outbox is the durable record — a warning that lived only on the page would disappear
while the incomplete file stayed in AnythingLLM forever.

**Very large output arrives as section files.** Above
`PDF2MD_SECTION_SPLIT_THRESHOLD_BYTES` the outbox receives
`{slug}--{hash12}--{ordinal}-{section}.md` rather than one enormous file. This is about
citations, not search quality: AnythingLLM chunks whatever it is given and ranks chunks,
so file boundaries do not change what it finds — but they do change what it names when it
answers. A 2000-page manual as one file cites "the manual"; as section files it cites the
chapter.

⚠️ **Re-converting a document replaces its own section files.** This is the only place the
stack deletes from the outbox, and it is deliberately narrow — only files this service
wrote, only for the document being re-converted. Without it, an engine upgrade that
detects headings differently would leave two contradictory versions of the same document
for AnythingLLM to cite.

**The part size is a starting value, not a measurement.** `PDF2MD_PART_MAX_PAGES=100` is
sized to the engine's 40-minute per-document ceiling with a safety factor. Measure seconds
per page on your own corpus and set it properly — the same discipline as
`DOCLING_MEM_LIMIT`.

---

## 6. Health and logs

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

## 7. How the isolation works, and how to prove it

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

## 8. Importing into AnythingLLM

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

## 9. Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Deploy fails trying to pull an image | The GHCR package is private, or the pinned digest does not exist | `docker logout ghcr.io && docker pull <image>`; if it asks for a credential, fix the package's visibility rather than storing a token |
| Deploy fails resolving the stack file | Wrong compose path or reference on the Repository form | The path is `deploy/docker-compose.yml`, relative to the repository root |
| Engine never reaches healthy | Models missing from the image, or the warm-up is slower than the start period | `ops/verify-engine-image.sh`; the engine log for a download attempt |
| Conversions work but the page is unreachable from the LAN | The networks were consolidated onto `internal` — published ports do not work there | `docker network inspect`; see research.md R1 |
| Host becomes sluggish under a batch | Engine memory or thread count too high | Lower `DOCLING_WORKERS`, keep `SHARE_MODELS` true, tighten `DOCLING_MEM_LIMIT` |
| A job sits at Converting forever | The watchdog is above the engine's own timeout | `PDF2MD_JOB_TIMEOUT_SECONDS` must stay above `DOCLING_MAX_DOCUMENT_TIMEOUT`, and both must be finite |
| Database errors after a redeploy | SQLite was moved onto a bind mount | It belongs on the named volume |
| The stack redeployed on its own | GitOps updates were switched on | Turn them off; the deployed version is meant to change only when a person decides it should |

---

## 10. Version pinning, and what has been verified

Both images are pinned by tag **and** digest. The tag is for humans; the digest is the
actual pin, because a tag can be re-pointed upstream and a digest cannot.

```
ghcr.io/docling-project/docling-serve-cpu:v1.18.0
  @sha256:6aa1b1428b5c83db2a4fc3431d99902ef115d9e1ce13eed0f716d23ed9d9a098

ghcr.io/marrothm/pdf2md-web:1.0.0
  built by .github/workflows/publish.yml on a v* tag; digest in that job's summary
```

Verified against the engine tag before deployment:

| Claim | How it was checked |
|---|---|
| The tag exists for `linux/arm64` | Registry manifest list |
| The image expects models at `/opt/app-root/src/.cache/docling/models` | `DOCLING_SERVE_ARTIFACTS_PATH` in the image's own environment |
| Tesseract data ships with it | `TESSDATA_PREFIX` in the image's environment |
| `/ready` gates on model loading; `/health` only reports the process is up; neither needs the API key | The pinned tag's source |
| `/v1/convert/file/async`, `/v1/status/poll/{id}`, `/v1/result/{id}` exist, and `X-Api-Key` is the header name | The pinned tag's source |

**Still to verify on the Mac mini itself**, because it cannot be checked from a registry:
that the model directory in the pulled image is actually populated. Run
`ops/verify-engine-image.sh` after the first deploy. A `-slim` variant fails there, which
is the point — it would otherwise deploy cleanly, report healthy, and fail on the first
scanned page.

**Upgrading the engine is a deliberate act**, never an automatic one: pick the new tag,
resolve its `linux/arm64` digest, put it in `deploy/docker-compose.yml`, commit, redeploy,
then re-run `ops/measure-fidelity.py`. An upgrade that quietly changes layout analysis
shows up there and nowhere else.

---

## 11. Measurements to record here

These come from the deployed stack and belong in this file once run, so the next person
inherits numbers rather than assumptions.

| What | How | Result |
|---|---|---|
| Engine memory under a 50-document batch, and the `DOCLING_MEM_LIMIT` it implies | `docker stats` during quickstart V9 | _not yet measured — current limit 5g is a starting value_ |
| 20-page text PDF conversion time (SC-003: under 3 minutes) | Convert one and time it | _not yet measured_ |
| Clean-host deploy time from this document alone (SC-004: under 30 minutes) | Time a deploy on a fresh host | _not yet measured_ |
| Fidelity against the corpus (SC-001, SC-002, FR-004) | `python3 ops/measure-fidelity.py --base-url http://<ip>:8080` | _not yet measured — corpus not assembled_ |
| AnythingLLM spot checks (SC-009: ≥9 of 10 cite the right document) | Import the outbox, ask 10 questions | _not yet measured_ |
