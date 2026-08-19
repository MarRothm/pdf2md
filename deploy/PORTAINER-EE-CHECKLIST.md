# Portainer EE deployment checklist

The step-by-step version of [`README.md`](./README.md), written to be worked through
top to bottom with a terminal open on the Mac mini and Portainer EE in a browser tab.
Tick every box. Nothing here is optional — the boxes that look like formalities
(`re-pull` off, the outbox directory existing beforehand, the two verify scripts) are
the ones that fail deploys on an air-gapped host.

Measured target: OrbStack on `linux/arm64` with `portainer/portainer-ee` already
running. Substitute the Mac mini's LAN address for `10.0.0.19` throughout.

**Time budget:** ~20 minutes on the connected machine, ~15 on the Mac mini, plus a
first engine start that takes minutes on its own.

---

## Part 0 — Before Portainer (connected machine)

- [ ] Repository checked out at the commit you intend to ship, `git status` clean.
- [ ] `./ops/save-images.sh` completes. It aborts on a `latest` tag, on a `-slim`
      engine variant, and — the check that matters — on an engine image whose
      `/opt/app-root/src/.cache/docling/models` is empty. An empty model directory is
      a stack that deploys fine and fails on its first conversion.
- [ ] `dist/` contains `docling-serve-cpu.tar.gz`, `pdf2md-web.tar.gz`, `SHA256SUMS`,
      and `IMAGES` (~2 GB total).
- [ ] Archives moved to the Mac mini out of band (USB, LAN copy).

## Part 1 — Before Portainer (Mac mini shell)

This is the only host-shell work. Everything after it happens in the UI.

- [ ] Outbox directory exists and is writable by you:
      ```bash
      mkdir -p ~/pdf2md-outbox && touch ~/pdf2md-outbox/.probe && rm ~/pdf2md-outbox/.probe
      ```
      Create it *before* deploying. If the bind-mount source is missing at deploy time
      Docker creates it root-owned, and you will not be able to open it in Finder.
- [ ] Absolute path written down for `OUTBOX_HOST_PATH` — `/Users/you/pdf2md-outbox`,
      not `~/pdf2md-outbox`. Portainer does not expand `~`.
- [ ] At least ~8 GB free for the loaded images, plus room for the outbox:
      `df -h /`
- [ ] `./ops/load-images.sh /path/to/dist` passes. It verifies checksums *before*
      loading and asserts both pinned tags exist afterwards.
- [ ] Both tags visible: `docker images | grep -E 'docling-serve-cpu|pdf2md-web'`
- [ ] Engine API key generated and stored where you will find it again:
      `openssl rand -hex 32`
- [ ] LAN address noted: `ipconfig getifaddr en0`

## Part 2 — Portainer EE session

- [ ] Logged in as a user with permission to create stacks (an administrator, or an
      Operator/Standard user with edit rights on this environment).
- [ ] EE licence is valid and not expired — **Settings → Licenses**. An expired
      licence degrades the UI and is a confusing thing to discover mid-deploy.
- [ ] Correct environment selected from **Environments** / the home view — the local
      Docker Standalone environment backed by OrbStack. In EE the environment is a
      first-class selector; the **Stacks** menu applies to whichever one is active.
- [ ] Environment type is **Docker Standalone**, not Swarm. The compose file uses
      `mem_limit` and `pull_policy`, which are standalone semantics.

## Part 3 — Create the stack

- [ ] **Stacks → Add stack**.
- [ ] Name: `pdf2md` — lowercase, exactly this. It becomes the compose project name and
      both `ops/verify-*.sh` scripts filter on
      `label=com.docker.compose.project=pdf2md`. A different name means passing it as
      the scripts' argument every time.
- [ ] Build method: **Web editor**. ⚠️ Not **Repository** — the Git method needs egress
      to the remote at deploy time, which this host does not have.
- [ ] Contents of [`docker-compose.yml`](./docker-compose.yml) pasted in whole. Do not
      hand-edit it in the editor; edit it in the repository and re-paste, or the
      deployed stack stops matching what is in version control.
- [ ] `networks:` block pasted unchanged. `core` must stay `internal: true` and `edge`
      must keep `enable_ip_masquerade: "false"`. That block is the security posture of
      this stack; nothing else enforces it.

## Part 4 — Environment variables

In **Environment variables**, add the two required ones. Everything else has a working
default — see [`.env.example`](./.env.example) for the full list and the reasoning
behind each value.

| Variable | Value | Why it has no default |
|---|---|---|
| `PDF2MD_ENGINE_API_KEY` | the string from Part 1 | `${...:?}` — deploy fails loudly rather than shipping an unauthenticated engine |
| `OUTBOX_HOST_PATH` | `/Users/you/pdf2md-outbox` | `${...:?}` — a wrong guess would silently put the durable record inside a container |

- [ ] Both variables present, no surrounding quotes, no trailing whitespace.
- [ ] If your version offers **Advanced mode** or **Load variables from .env file**, a
      paste of `KEY=value` lines is equivalent — check afterwards that it parsed into
      individual rows.
- [ ] `WEB_PORT` set only if 8080 is taken on this host. If you change it, every
      command in this checklist and both verify scripts need the new port.
- [ ] Nothing else overridden on a first deploy. Tune `DOCLING_WORKERS`,
      `DOCLING_MEM_LIMIT`, and `OMP_NUM_THREADS` after you have measured a real batch
      (Part 8), not before.

## Part 5 — EE options on the Add stack form

These are the settings whose defaults are wrong, harmless-looking, or version-dependent.

- [ ] **Re-pull image / pull latest image version: OFF.** ⚠️ With no egress a pull
      attempt is a failed deploy — Portainer does not fall back to the local image.
      `pull_policy: never` in the compose file says the same thing a second time; leave
      both in place.
- [ ] **Webhook: OFF.** It exists to let a repository trigger redeployments. There is no
      repository here, and an unused inbound trigger is not something this stack should
      carry.
- [ ] **Registries:** leave unselected/default. Nothing is pulled, so credentials are
      irrelevant — but do not attach a registry that would tempt a future redeploy into
      trying.
- [ ] **GitOps updates / automatic updates: OFF** (only offered on the Repository
      method; if you see it, you are on the wrong build method — go back to Part 3).
- [ ] **Access control:** in EE this is where stack ownership is set. Restrict to
      administrators, or to the team that operates this host. Presence on the LAN is
      authorisation for *converting documents* (FR-024); it is not authorisation for
      redeploying the stack.

## Part 6 — Deploy and watch

- [ ] **Deploy the stack** clicked. It should return without a pull error. If it fails
      complaining about an image, stop: re-pull was left on, or `ops/load-images.sh`
      did not run here.
- [ ] Both containers appear under **Containers** with project `pdf2md`.
- [ ] `docling` reaches **healthy**. First start warms the models, so expect a few
      minutes. Its healthcheck allows a 180 s start period, then 5 × 30 s of retries —
      if it has not gone healthy by roughly six minutes, treat it as a failure and read
      its log.
- [ ] Engine log (Portainer → Containers → `pdf2md-docling-1` → Logs) shows model
      loading and **no download attempt**. Any mention of huggingface or name
      resolution means the image shipped without weights; go back to Part 0.
- [ ] `web` reaches **healthy** after `docling` does — it waits on
      `depends_on: service_healthy`, so it never comes up against a cold engine.
- [ ] Neither container is restart-looping (restart count stays 0 in the container
      list).

## Part 7 — Verify before handing it over

Run all four. The first two are the deploy; the last two are the reason it is safe.

- [ ] Health is `ok`:
      ```bash
      curl -s http://10.0.0.19:8080/api/health
      ```
      `"status": "ok"`, `engine.reachable: true`, `outbox.writable: true`. If
      `outbox.writable` is false, `OUTBOX_HOST_PATH` is wrong or root-owned — fix it on
      the host and redeploy, do not chmod inside the container.
- [ ] Page opens from **another machine** on the LAN: `http://10.0.0.19:8080`
- [ ] One real conversion end to end: upload a PDF with a multi-column page, a table,
      and a scanned page. It reaches **Converted**, and the `.md` appears in
      `~/pdf2md-outbox` named `{slug}--{hash12}.md`.
- [ ] `./ops/verify-offline.sh` → PASS. Egress blocked from both services, `web →
      docling:5001` reachable, engine publishes nothing, no downloads in the log.
- [ ] `./ops/verify-lan-only.sh 10.0.0.19` → PASS, including the three things the script
      cannot check for you: unreachable from outside the network, no port-forward or
      reverse proxy, no tunnelling service.
- [ ] Restart survival: reboot the Mac mini, confirm the stack returns to healthy
      unattended and the job list and outbox are intact.

**Re-run both verify scripts after any change to `networks:`, `ports:`, or the engine's
environment.** A Portainer redeploy carries the stack file and nothing else, so anything
you configured outside it is not part of the deployment.

## Part 8 — Record what you measured

The stack is deployed; the numbers it was deployed under are still unknown. Fill in the
table in [`README.md` §10](./README.md) so the next person inherits measurements rather
than assumptions:

- [ ] Engine memory under a 50-document batch (`docker stats`) — this is what sets the
      final `DOCLING_MEM_LIMIT`, not guesswork.
- [ ] Conversion time for a 20-page text PDF (target: under 3 minutes).
- [ ] Fidelity: `ops/measure-fidelity.py --base-url http://10.0.0.19:8080`
- [ ] AnythingLLM spot check after importing the outbox (target: ≥9 of 10 answers cite
      the right document).
- [ ] Date, image tags, and stack name written down with the numbers.

---

## Redeploy checklist (variable change or new web image)

- [ ] New image loaded on the host first, if the web image changed
      (`ops/load-images.sh`), and `WEB_IMAGE` bumped to the new tag.
- [ ] Portainer → **Stacks → `pdf2md` → Editor**, change, **Update the stack**.
- [ ] **Re-pull image: still OFF.**
- [ ] **Prune services** only if you removed a service from the file — this stack has
      exactly two, so normally off.
- [ ] Both services healthy again, `/api/health` `ok`.
- [ ] If `networks:`, `ports:`, or the engine environment changed: both verify scripts
      re-run.

Job history and converted documents survive a redeploy — they live on the `db` and
`inbox` volumes and in the outbox directory, never in container layers (FR-017).

## If you need to back out

- [ ] **Stop** the stack from its controls (do not delete it — deleting offers to remove
      volumes, and the job registry lives on one).
- [ ] Re-deploy the previous stack file from the **Version** dropdown in the Editor, or
      re-paste the previous `docker-compose.yml` from git.
- [ ] The outbox is untouched by any of this. It is a bind mount on the host and nothing
      in the stack ever deletes from it.

## When a box will not tick

| Symptom | Likely cause | Check |
|---|---|---|
| Deploy fails trying to pull | Re-pull left on, or the tag is missing locally | `docker images`; re-run `ops/load-images.sh`; redeploy with the toggle off |
| Engine never reaches healthy | Models missing from the image, or warm-up slower than the start period | Engine log for a download attempt; re-run `ops/save-images.sh` — its model check would have caught it |
| `outbox.writable: false` | `OUTBOX_HOST_PATH` missing at deploy time, so Docker created it root-owned | `ls -ld` the path on the host; recreate as your user, redeploy |
| Conversions work, page unreachable from the LAN | Networks consolidated onto `internal` — published ports do not work there | `docker network inspect`; research.md R1 |
| A job sits at Converting forever | Watchdog above the engine's own timeout | `PDF2MD_JOB_TIMEOUT_SECONDS` must stay above `DOCLING_MAX_DOCUMENT_TIMEOUT`, both finite |
| Database errors after redeploy | SQLite moved onto a bind mount | It belongs on a named volume (research.md R7) |

Fuller versions of all of these are in [`README.md` §8](./README.md) and
[`../specs/001-docling-pdf2md-stack/quickstart.md`](../specs/001-docling-pdf2md-stack/quickstart.md).
