# Contract: Portainer Stack Definition

**Feature**: `001-docling-pdf2md-stack` | **Artifact**: `deploy/docker-compose.yml`

This is the single definition the operator deploys (FR-015), read by Portainer directly from this repository at `deploy/docker-compose.yml` (FR-030). Everything about isolation is expressed here, not in host firewall rules, so a redeploy cannot lose it.

## Deployment source

| Portainer field | Value |
|---|---|
| Build method | **Repository** (not Web editor) |
| Repository URL | this repository's HTTPS URL |
| Authentication | **off** — the repository is public (FR-031) |
| Repository reference | `refs/heads/main` |
| Compose path | `deploy/docker-compose.yml` |
| GitOps updates | **off** — no polling, no webhook (FR-032, research.md R5) |
| Environment variables | `PDF2MD_ENGINE_API_KEY` and `OUTBOX_HOST_PATH` as stack variables; the compose file keeps its `${...}` placeholders |

The compose path is part of this contract. Portainer resolves it inside the cloned repository, so moving the file is a breaking change to every existing deployment.

## Services

| Service | Image | Networks | Ports | Purpose |
|---|---|---|---|---|
| `web` | `ghcr.io/<owner>/pdf2md-web:<version>@sha256:<digest>` (built by CI on arm64, research.md R10) | `edge`, `core` | `${WEB_PORT:-8080}:8080` | Page, API, job registry, outbox writes |
| `docling` | `ghcr.io/docling-project/docling-serve-cpu:<pinned-tag>@sha256:<digest>` (upstream, unmodified) | `core` only | none | Conversion engine |

Both carry `pull_policy: missing` and `restart: unless-stopped` (FR-016).

Both images are pinned by tag **and** digest. The digest is what makes the pin real — a tag can be re-pointed upstream, a digest cannot (FR-032). `pull_policy: missing` then means a redeploy reuses the local image when the digest already matches, so only an intentional version change costs a download.

**One digest, one place.** `deploy/docker-compose.yml` is the only file in which either digest is written down, and `.github/workflows/publish.yml` writes the web one itself: on a `v*` tag it builds the image, pushes it, rewrites the pin, and commits that to `main`. A redeploy therefore always deploys the newest published release without anyone transcribing a digest.

`deploy/.env.example` previously carried its own copy of both references, which made a release transcribe the same 71-character string into two files with nothing checking that they agreed — and a value in the environment silently outranks the stack file, so a stale copy there would quietly pin the deployment backwards. The two variables remain documented as deliberate overrides, commented out, and `tests/unit/test_compose_pins.py` fails if a digest reappears there.

This does not weaken FR-032. The image is still pinned by digest, still never `latest`, and nothing redeploys itself: GitOps updates stay off and a person still triggers *Pull and redeploy*. What became automatic is the recording of what was built, not the decision to deploy it.

**Registry retention: two versions.** `.github/workflows/prune-images.yml` deletes every `pdf2md-web` version in GHCR except the newest two — the current release and one step back — and removes untagged build residue. It runs after each successful publish and can be run by hand.

The rule is lossy on purpose, and the loss is worth stating plainly: GHCR is the only copy of an image. A version older than latest-1 is gone, so there is exactly **one rollback step**. A container already running a deleted version keeps running, because `pull_policy: missing` reuses the local image, but that version can never be pulled again — no rollback to it, and no rebuilding the Mac mini at it. Two releases without a redeploy is therefore enough to make the running image unobtainable. Accepted knowingly (2026-08-20) over the alternative of also retaining whatever digest the stack file pins.

## Networks

```yaml
networks:
  edge:
    driver: bridge
    driver_opts:
      com.docker.network.bridge.enable_ip_masquerade: "false"
  core:
    internal: true
```

**This is the isolation mechanism** (FR-021, FR-023), validated on the target host during planning:

- `core` has no default route, so `docling` cannot reach anything outside the stack. Attempts fail immediately with "Network unreachable".
- `edge` has no SNAT, so `web`'s egress blackholes while its published port still accepts LAN traffic. Measured: HTTP 200 from the LAN IP, egress to `1.1.1.1` times out.
- Putting everything on `internal` was tested and **breaks published ports entirely** — do not "simplify" the stack that way (research.md R1).

## Volumes

```yaml
volumes:
  db:      # named — SQLite must not live on a macOS bind mount
  inbox:   # named — uploaded PDFs
```

| Mount | Type | Container path | Notes |
|---|---|---|---|
| `db` | named volume | `/data/db` | Job registry; survives redeploy (FR-017) |
| `inbox` | named volume | `/data/inbox` | Uploaded PDFs, reaped after retention |
| `${OUTBOX_HOST_PATH}` | **bind mount** | `/data/outbox` | The AnythingLLM handoff folder the operator opens in Finder (FR-013) |

`${OUTBOX_HOST_PATH}` is the one host-side preparation step: the directory must exist and be writable before the first deploy (FR-015 permits exactly this).

## Environment — `web`

| Variable | Default | Purpose |
|---|---|---|
| `PDF2MD_ENGINE_URL` | `http://docling:5001` | Engine address on `core` |
| `PDF2MD_ENGINE_API_KEY` | *(required)* | Sent as `X-Api-Key` |
| `PDF2MD_DB_PATH` | `/data/db/pdf2md.sqlite` | |
| `PDF2MD_INBOX_PATH` | `/data/inbox` | |
| `PDF2MD_OUTBOX_PATH` | `/data/outbox` | |
| `PDF2MD_MAX_UPLOAD_BYTES` | `209715200` (200 MB) | Must stay ≤ the engine's `MAX_FILE_SIZE` |
| `PDF2MD_JOB_TIMEOUT_SECONDS` | `2700` (45 min) | Watchdog, applied **per part**; a document's ceiling is `part_count ×` this. Set above the engine's per-document timeout so the engine gives up first. Applied per document it would kill every split document (research.md R12) |
| `PDF2MD_POLL_INTERVAL_SECONDS` | `2` | Engine polling cadence |
| `PDF2MD_INBOX_RETENTION_HOURS` | `48` | PDF reaping after a job succeeds |
| `PDF2MD_FAILED_INBOX_RETENTION_DAYS` | `14` | Failed and timed-out jobs keep their source PDF this long so a retry is possible |
| `PDF2MD_SUSPECT_MIN_CHARS_PER_PAGE` | `50` | Below this yield a conversion reports as suspect rather than plain success (FR-029) |
| `PDF2MD_JOB_HISTORY_DAYS` | `30` | History pruning; never touches the outbox |
| `PDF2MD_PART_MAX_PAGES` | `40` | Documents longer than this are split. A part has to fit inside every ceiling at once — the engine's 2400 s per submission, the memory each container is given, and the engine's page limit — and which one binds is a property of the corpus, not of this setting. Measured on the real corpus: ~0.6 s/page for born-digital text, 2–6.5 s/page for pages of images and spreadsheet layouts. The former default of `100` was derived from an assumed 10 s/page that was never measured (research.md R6, R12) |
| `PDF2MD_MAX_TOTAL_PAGES` | `10000` | Refused at upload above this (FR-036). With `PARTS_IN_FLIGHT` already bounding queue slots, this ceiling mostly bounds **wall-clock time**: 10 000 pages is 100 parts, roughly 14 hours at two in flight. Set it to the longest single document worth occupying the converter for |
| `PDF2MD_PARTS_IN_FLIGHT` | `2` | Parts of one document in the engine at once, so a long document does not starve short ones |
| `PDF2MD_ENGINE_WORKERS` | `${DOCLING_WORKERS}` | Mirrors the engine's worker count so the dispatcher submits no more documents than the engine can work on, plus a buffer of one (FR-027). Set from the same stack variable, so the two cannot drift |
| `PDF2MD_PART_RETRY_SPLITS` | `2` | How often a part that failed may be halved and tried again before the range is reported missing (FR-038). Bounded: each attempt costs the engine another run at it |
| `PDF2MD_PART_MIN_PAGES` | `10` | A part this small is not halved again — below it the pages, not their number, are the problem (FR-038) |
| `PDF2MD_PART_MAX_ATTEMPTS` | `3` | Attempts per part when the engine loses the task or the result. Spent attempts fall through to halving rather than to a gap, because an engine dying on a range presents as a lost task (FR-038) |
| `PDF2MD_JOB_MAX_ATTEMPTS` | `8` | Recoveries of one document before it is given up on. A document that is itself causing the restarts is otherwise recovered, stops the service again, and is recovered again — a loop no operator can escape from the page, because the page is down too (FR-042) |
| `PDF2MD_EXTRACT_IMAGES` | `true` | Pictures are written as files and referenced from the Markdown. Off, the Markdown still carries no picture data — it carries nothing where a picture was. Neither setting puts pictures back inside the Markdown, because the knowledge base cannot ingest a document that contains them (feature 003 FR-001, FR-010) |
| `PDF2MD_IMAGE_PAGE_COVERAGE` | `0.8` | Fraction of its page a picture must cover to be treated as the page rather than a figure. A scanned page is not extracted; its text is already in the Markdown (FR-004). Reasoned, not measured — quickstart V8 |
| `PDF2MD_IMAGE_HEADER_BAND` | `0.12` | A picture lying entirely within this fraction of the page from the top is the page's furniture — a party logo — and is not extracted (feature 003 FR-014). Position separates furniture from figures where size never could: the same logo appears at many sizes |
| `PDF2MD_IMAGE_FOOTER_BAND` | `0.12` | The same, measured from the bottom |
| `PDF2MD_IMAGE_MIN_BYTES` | `4096` | Below this a picture is a rule, a bullet, or a spacer (FR-005) |
| `PDF2MD_IMAGE_MAX_PER_DOCUMENT` | `500` | Safety net for a document that defeats the coverage rule; past it pictures are skipped and the Markdown says so (FR-005, FR-006) |
| `PDF2MD_OCR_PRESET` | `easyocr` | Which OCR engine the converter uses for scanned pages. `auto` picks RapidOCR, whose bundled weights read English and Chinese; `easyocr` needs nothing extra, since its `craft` and `latin_g2` weights are baked into the pinned image and `latin_g2` covers German (FR-039, research.md R4) |
| `PDF2MD_OCR_LANG` | `de,en` | Recognition languages, comma-separated, empty to take the engine's own default. Must be satisfiable from weights already in the image: nothing is downloaded at runtime (FR-022, FR-039) |
| `PDF2MD_MIN_FREE_BYTES` | `67108864` | Uploads are refused below this much free space, naming the location that is full |
| `PDF2MD_SECTION_SPLIT_THRESHOLD_BYTES` | `1048576` | Above this, output is written as section files (FR-033) |
| `PDF2MD_SECTION_MIN_BYTES` | `16384` | Sections smaller than this merge into the previous one |
| `PDF2MD_SECTION_MAX_BYTES` | `524288` | Sections larger than this are divided at the next heading level |
| `PDF2MD_LOG_LEVEL` | `INFO` | Job-level logs viewable in Portainer (FR-019) |

## Environment — `docling`

| Variable | Value | Why |
|---|---|---|
| `DOCLING_SERVE_ARTIFACTS_PATH` | `/opt/app-root/src/.cache/docling/models` | Baked-in models; guarantees no runtime download (FR-022) |
| `DOCLING_SERVE_LOAD_MODELS_AT_BOOT` | `true` | Warm at startup so the first conversion is not slow; supports SC-007 |
| `DOCLING_SERVE_ENABLE_REMOTE_SERVICES` | `false` | No outbound vision-model calls (FR-021) |
| `DOCLING_SERVE_ALLOW_EXTERNAL_PLUGINS` | `false` | No third-party plugin fetches (FR-021) |
| `DOCLING_SERVE_ENABLE_UI` | `false` | Upstream's Gradio demo UI is not our page (research.md R2) |
| `DOCLING_SERVE_API_KEY` | `${PDF2MD_ENGINE_API_KEY}` | Defense against accidental port publication (research.md R9) |
| `DOCLING_SERVE_ENG_KIND` | `local` | No Redis; the local engine is sufficient at this scale |
| `DOCLING_SERVE_ENG_LOC_NUM_WORKERS` | `2` | Bounded concurrent work (FR-027) |
| `DOCLING_SERVE_ENG_LOC_SHARE_MODELS` | `true` | Threads share one model set. Cheap, and it keeps the working set flat as workers are added — but no longer load-bearing: the VM has 42.1 GB (research.md R6) |
| `DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT` | `2400` (40 min) | Per-document ceiling (FR-028); default is 7 days |
| `DOCLING_SERVE_MAX_FILE_SIZE` | `209715200` | Matches the web upload limit |
| `DOCLING_SERVE_MAX_NUM_PAGES` | `2000` | Bounds a pathological document |
| `DOCLING_SERVE_SINGLE_USE_RESULTS` | `true` (default) | Kept; the client is designed around it |
| `DOCLING_SERVE_QUEUE_MAX_SIZE` | sized to the batch ceiling (≥50, SC-008) | Backpressure rather than unbounded growth |
| `DOCLING_DEVICE` | `cpu` | No GPU/MPS from Linux containers on macOS; explicit beats `auto` |
| `OMP_NUM_THREADS` | `4` (image default) | Leaves CPU headroom for the web service and Portainer (SC-011) |
| `DOCLING_SERVE_LOG_LEVEL` | `INFO` | Readable failure detail in Portainer logs (FR-019) |

## Resource limits

| Service | Limit | Rationale |
|---|---|---|
| `docling` | `16g` | Set clear of any plausible working set, not tuned to one. The VM has 42.1 GB and Portainer shares it, so memory is not the scarce resource — **CPU is** (research.md R6) |
| `web` | `4g` | It streams uploads and writes files, and holds real data in exactly one place: joining a long document out of its parts (FR-034) |

**A cap near the working set protects nothing.** A container's own `mem_limit` kills it however
much the host has spare, and for the engine each kill takes its task table with it, failing every
part in flight. That is the whole of the missing-pages history in this repository: `docling` at
`5g` and `web` at `512m` were both killed doing ordinary work while 37 GB of the VM sat unused.
Size these against the VM, not against a guess at the working set; if the host needs protecting
from a batch, the lever is `DOCLING_WORKERS` and `OMP_NUM_THREADS` (SC-011).

## Healthchecks

| Service | Check | Effect |
|---|---|---|
| `web` | `GET /healthz` | Portainer shows accurate health (FR-018) |
| `docling` | engine health endpoint (path to confirm, research.md O1) | `web` `depends_on: docling: condition: service_healthy` so the page never starts against a cold engine |

## Deployment invariants

1. No service other than `web` publishes a port.
2. No image tag is `latest`; every image is pinned by tag **and** digest (FR-032).
3. GitOps updates stay **off**. Nothing redeploys this stack but a person (FR-032). "Re-pull image" is an option of the GitOps update mechanism and therefore does not arise.
4. The stack file contains no secret values; `PDF2MD_ENGINE_API_KEY` comes from Portainer's stack environment variables, and the repository holds no secret at any point in its history (FR-031).
5. Deployment requires no credential — neither a Git credential in Portainer nor a registry login on the host (FR-031). If a deploy ever asks for one, something is wrong with the repository or package visibility; storing a token is not the fix.
6. Changing `networks:` is a change to the security posture of the stack and must be re-verified with `ops/verify-offline.sh` and `ops/verify-lan-only.sh` (FR-026).
