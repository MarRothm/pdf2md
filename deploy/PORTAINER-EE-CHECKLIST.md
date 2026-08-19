# Portainer EE deployment checklist

The step-by-step version of [`README.md`](./README.md), written to be worked through top
to bottom with a terminal open on the Mac mini and Portainer EE in a browser tab. Tick
every box. The ones that look like formalities — GitOps off, the outbox directory
existing beforehand, the three verify scripts — are the ones that bite later.

Measured target: OrbStack on `linux/arm64` with `portainer/portainer-ee` already running.
Substitute the Mac mini's LAN address for `10.0.0.19` throughout.

**Time budget:** ~10 minutes of setup, ~5 minutes in the Portainer UI, plus a first
deploy that pulls ~4.6 GB and then warms the engine's models.

---

## Part 0 — Before Portainer (anywhere)

Deployment reads from GitHub and pulls from GHCR, so what has to be true is about the
repository, not about this host.

- [ ] Repository is **public**. Portainer will deploy with the Authentication toggle off,
      and no credential ends up on the Mac mini (FR-031).
- [ ] The version you intend to deploy is published: a `v*` tag was pushed and
      `.github/workflows/publish.yml` completed.
- [ ] The image pulls with **no credential** — from any machine that has never logged in:
      ```bash
      docker logout ghcr.io
      docker pull ghcr.io/marrothm/pdf2md-web:1.0.0
      ```
      If this asks for credentials the package was created private. Fix its visibility in
      the package settings; do not solve it by storing a token.
- [ ] `deploy/docker-compose.yml` pins that image by digest, and the digest matches the
      one in the publish workflow's job summary.
- [ ] `main` is pushed. Portainer deploys what is on the branch, not what is on your
      laptop.

## Part 1 — Before Portainer (Mac mini shell)

This is the only host-shell work in the entire deployment.

- [ ] Outbox directory exists and is writable by you:
      ```bash
      mkdir -p ~/pdf2md-outbox && touch ~/pdf2md-outbox/.probe && rm ~/pdf2md-outbox/.probe
      ```
      Create it *before* deploying. If the bind-mount source is missing at deploy time,
      Docker creates it root-owned and you will not be able to open it in Finder.
- [ ] Absolute path written down for `OUTBOX_HOST_PATH` — `/Users/you/pdf2md-outbox`, not
      `~/pdf2md-outbox`. Portainer does not expand `~`.
- [ ] At least ~8 GB free for the pulled images, plus room for the outbox: `df -h /`
- [ ] The host can reach GHCR: `docker pull hello-world` succeeds. The *stack* will have
      no internet; the host needs it to deploy.
- [ ] Engine API key generated and stored where you will find it again:
      `openssl rand -hex 32`
- [ ] LAN address noted: `ipconfig getifaddr en0`

## Part 2 — Portainer EE session

- [ ] Logged in as a user with permission to create stacks.
- [ ] EE licence valid and not expired — **Settings → Licenses**. An expired licence
      degrades the UI and is a confusing thing to discover mid-deploy.
- [ ] Correct environment selected — the local Docker Standalone environment backed by
      OrbStack. In EE the environment is a first-class selector, and **Stacks** applies to
      whichever one is active.
- [ ] Environment type is **Docker Standalone**, not Swarm. The compose file uses
      `mem_limit` and `pull_policy`, which are standalone semantics.

## Part 3 — Create the stack

- [ ] **Stacks → Add stack**.
- [ ] Name: `pdf2md` — lowercase, exactly this. It becomes the compose project name, and
      all three `ops/verify-*.sh` scripts filter on
      `label=com.docker.compose.project=pdf2md`. A different name means passing it as the
      scripts' argument every time.
- [ ] Build method: **Repository**. ⚠️ Not **Web editor** — pasting the file means the
      deployed stack can drift from the repository, which is the thing this deployment
      method exists to prevent.
- [ ] Repository fields:

      | Field | Value |
      |---|---|
      | Repository URL | `https://github.com/MarRothm/pdf2md` |
      | Repository reference | `refs/heads/main` |
      | Compose path | `deploy/docker-compose.yml` |

- [ ] Compose path typed exactly. Portainer resolves it inside the cloned repository;
      it is part of the deployment contract, not a convenience.

## Part 4 — Environment variables

Add the two required ones. Everything else has a working default — see
[`.env.example`](./.env.example) for the full list and the reasoning behind each value.

| Variable | Value | Why it has no default |
|---|---|---|
| `PDF2MD_ENGINE_API_KEY` | the string from Part 1 | `${...:?}` — deploy fails loudly rather than shipping an unauthenticated engine |
| `OUTBOX_HOST_PATH` | `/Users/you/pdf2md-outbox` | `${...:?}` — a wrong guess would silently put the durable record inside a container |

- [ ] Both variables present, no surrounding quotes, no trailing whitespace.
- [ ] `WEB_PORT` set only if 8080 is taken on this host. If you change it, every command
      in this checklist and both LAN verify scripts need the new port.
- [ ] Nothing else overridden on a first deploy. Tune `DOCLING_WORKERS`,
      `DOCLING_MEM_LIMIT`, and `OMP_NUM_THREADS` after you have measured a real batch
      (Part 8), not before.
- [ ] `ENGINE_IMAGE` and `WEB_IMAGE` **not** overridden. Overriding them deploys something
      other than what the repository pins, which is exactly what the digests are for.

## Part 5 — The options whose defaults are wrong here

- [ ] **Authentication: OFF.** The repository is public. If Portainer asks for a
      credential, something is wrong with the repository's visibility — storing a token is
      not the fix (FR-031).
- [ ] **GitOps updates: OFF.** ⚠️ Both mechanisms are wrong for this stack. *Polling*
      changes the converting version without anyone deciding to, possibly mid-batch, and
      an engine upgrade quietly changes layout analysis — a change that surfaces as
      degraded retrieval weeks later, nowhere near the deploy. *Webhook* requires GitHub's
      runners to reach Portainer, meaning inbound exposure, which contradicts the whole
      LAN-only posture.
- [ ] **Registries:** leave unselected. The images are public; no credential applies.
- [ ] **Enable relative path volumes: OFF.** The only host path here is the outbox, and it
      comes from a stack variable as an absolute path.
- [ ] **Access control:** in EE this is where stack ownership is set. Restrict to
      administrators, or to the team that operates this host. Presence on the LAN is
      authorisation for *converting documents*; it is not authorisation for redeploying
      the stack.

## Part 6 — Deploy and watch

- [ ] **Deploy the stack** clicked. Portainer clones the repository, then the daemon pulls
      ~4.4 GB of engine and ~200 MB of web image. The first deploy is slow for that reason
      alone.
- [ ] Both containers appear under **Containers** with project `pdf2md`.
- [ ] `docling` reaches **healthy**. After the pull, it warms its models, so expect a few
      minutes. Its healthcheck allows a 180 s start period then 5 × 30 s of retries — if it
      has not gone healthy by roughly six minutes past start, treat it as a failure and
      read its log.
- [ ] Engine log (Portainer → Containers → `pdf2md-docling-1` → Logs) shows model loading
      and **no download attempt**. Any mention of huggingface or name resolution means the
      image shipped without weights.
- [ ] `web` reaches **healthy** after `docling` does — it waits on
      `depends_on: service_healthy`, so it never comes up against a cold engine.
- [ ] Neither container is restart-looping (restart count stays 0 in the container list).

## Part 7 — Verify before handing it over

Run all five. The first three are the deploy; the last two are the reason it is safe.

- [ ] Health is `ok`:
      ```bash
      curl -s http://10.0.0.19:8080/api/health
      ```
      `"status": "ok"`, `engine.reachable: true`, `outbox.writable: true`. If
      `outbox.writable` is false, `OUTBOX_HOST_PATH` is wrong or root-owned — fix it on the
      host and redeploy; do not chmod inside the container.
- [ ] The deployed engine is the pinned one, and carries its models:
      ```bash
      ./ops/verify-engine-image.sh
      ```
      This is the check that catches an engine which deploys cleanly, reports healthy, and
      then fails on the first scanned page.
- [ ] Page opens from **another machine** on the LAN: `http://10.0.0.19:8080`
- [ ] One real conversion end to end: upload a PDF with a multi-column page, a table, and a
      scanned page. It reaches **Converted**, and the `.md` appears in `~/pdf2md-outbox`
      named `{slug}--{hash12}.md`.
- [ ] `./ops/verify-offline.sh` → PASS. Egress blocked from both containers,
      `web → docling:5001` reachable, engine publishes nothing, no downloads in the log.
      **The host pulled from the internet minutes ago; this proves the containers cannot.**
- [ ] `./ops/verify-lan-only.sh 10.0.0.19` → PASS, including the three things the script
      cannot check for you: unreachable from outside the network, no port-forward or
      reverse proxy, no tunnelling service.
- [ ] Restart survival: reboot the Mac mini, confirm the stack returns to healthy
      unattended and the job list and outbox are intact.

**Re-run the two isolation scripts after any change to `networks:`, `ports:`, or the
engine's environment.** A redeploy carries the repository's stack file and nothing else,
so anything configured outside it is not part of the deployment.

## Part 8 — Record what you measured

The stack is deployed; the numbers it was deployed under are still unknown. Fill in the
table in [`README.md` §11](./README.md) so the next person inherits measurements rather
than assumptions:

- [ ] Engine memory under a 50-document batch (`docker stats`) — this is what sets the
      final `DOCLING_MEM_LIMIT`, not guesswork.
- [ ] Conversion time for a 20-page text PDF (target: under 3 minutes).
- [ ] Fidelity: `ops/measure-fidelity.py --base-url http://10.0.0.19:8080`
- [ ] AnythingLLM spot check after importing the outbox (target: ≥9 of 10 answers cite the
      right document).
- [ ] Date, both image digests, and the stack name written down with the numbers.

---

## Redeploy checklist

- [ ] The change is committed and pushed to `main`. Portainer deploys the branch, not your
      working tree.
- [ ] If the version changed: the new image is published, and its digest is pinned in
      `deploy/docker-compose.yml`.
- [ ] Portainer → **Stacks → `pdf2md`** → pull and redeploy.
- [ ] **GitOps updates: still OFF.**
- [ ] Both services healthy again, `/api/health` `ok`.
- [ ] If the engine version changed: `ops/verify-engine-image.sh`, then
      `ops/measure-fidelity.py` — an engine upgrade changes layout analysis and nothing
      else will tell you.
- [ ] If `networks:`, `ports:`, or the engine environment changed: both isolation scripts
      re-run.

Job history and converted documents survive a redeploy — they live on the `db` and
`inbox` volumes and in the outbox directory, never in container layers (FR-017).

## If you need to back out

- [ ] **Stop** the stack from its controls. Do not delete it — deleting offers to remove
      volumes, and the job registry lives on one.
- [ ] Revert the pin in `deploy/docker-compose.yml` to the previous digest, push, and
      redeploy. The previous image is still on the host, so this costs no download.
- [ ] The outbox is untouched by any of this. It is a bind mount on the host, and nothing
      in the stack ever deletes from it.

## When a box will not tick

| Symptom | Likely cause | Check |
|---|---|---|
| Portainer asks for a Git credential | The repository is still private | Make it public; do not store a token (FR-031) |
| Deploy fails pulling an image | The GHCR package is private, or the pinned digest does not exist | `docker logout ghcr.io && docker pull <image>` by hand |
| Deploy fails resolving the stack file | Wrong compose path or reference | `deploy/docker-compose.yml`, relative to the repository root |
| Engine never reaches healthy | Models missing from the image, or warm-up slower than the start period | `ops/verify-engine-image.sh`; the engine log for a download attempt |
| `outbox.writable: false` | `OUTBOX_HOST_PATH` missing at deploy time, so Docker created it root-owned | `ls -ld` the path; recreate as your user, redeploy |
| Conversions work, page unreachable from the LAN | Networks consolidated onto `internal` — published ports do not work there | `docker network inspect`; research.md R1 |
| A job sits at Converting forever | Watchdog above the engine's own timeout | `PDF2MD_JOB_TIMEOUT_SECONDS` must stay above `DOCLING_MAX_DOCUMENT_TIMEOUT`, both finite |
| Database errors after redeploy | SQLite moved onto a bind mount | It belongs on a named volume (research.md R7) |
| The stack redeployed by itself | GitOps updates were switched on | Turn them off (Part 5) |

Fuller versions of all of these are in [`README.md` §9](./README.md) and
[`../specs/001-docling-pdf2md-stack/quickstart.md`](../specs/001-docling-pdf2md-stack/quickstart.md).
