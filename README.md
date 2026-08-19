# pdf2md

An offline PDF-to-Markdown service for a Mac mini. Someone on the local network opens a
page, drops in a stack of PDFs, and gets Markdown that AnythingLLM can ingest as it is —
with no internet involved at any point, including the first start.

The documents that go through it are not ours to leak. That constraint shapes every
decision here: the conversion engine sits on a Docker network with no default route, the
web service's own egress blackholes, and the models are baked into the image rather than
fetched. Isolation is a property of the stack definition, so a redeploy cannot lose it.

## The two services

```
   LAN ──▶ :8080 ──▶ ┌─────┐              ┌─────────┐
                     │ web │──── core ────│ docling │
                     └─────┘              └─────────┘
                        │                      │
                    outbox/*.md            (models baked in)
                        │
                   operator imports
                   into AnythingLLM
```

**`docling`** is the upstream [`docling-serve-cpu`](https://github.com/docling-project/docling-serve)
image, unmodified and pinned to an exact tag. It does the conversion: layout analysis,
reading order across columns, table structure, OCR for scanned pages. It already provides
a queue, per-document timeouts, and an async task API, so none of that is reimplemented
here.

**`web`** is the small FastAPI service in this repository. It owns what upstream does not
provide: a dependency-free browser page, a durable job registry, content-addressed output
naming, and writing finished Markdown into the outbox folder the operator imports from.

## Where things are

| Path | What it is |
|---|---|
| `src/pdf2md/` | The web service — API, dispatcher, engine client, and the page it serves |
| `deploy/` | The Portainer stack definition, its variables, the operator's guide, and the deployment checklist |
| `ops/` | Air-gap transfer, isolation verification, and the fidelity harness |
| `tests/` | Unit, contract, and integration tests against a stub engine |
| `specs/001-docling-pdf2md-stack/` | Why everything is the way it is |

Start with [`deploy/README.md`](deploy/README.md) to run it —
[`deploy/PORTAINER-EE-CHECKLIST.md`](deploy/PORTAINER-EE-CHECKLIST.md) is the same thing as a
step-by-step checklist — and
[`specs/001-docling-pdf2md-stack/spec.md`](specs/001-docling-pdf2md-stack/spec.md) to
understand what it is meant to do. The design decisions, including the ones that were
tried and rejected, are in
[`plan.md`](specs/001-docling-pdf2md-stack/plan.md) and
[`research.md`](specs/001-docling-pdf2md-stack/research.md).

## Developing

The test suite runs against a stub engine, so no 4.4 GB image is needed to work on the
web service:

```bash
uv sync --extra dev                 # or: pip install -e '.[dev]'
pytest                              # unit, contract, and integration tests
ruff check src tests && ruff format --check src tests

PDF2MD_ENGINE_API_KEY=dev \
PDF2MD_DB_PATH=.local/db/pdf2md.sqlite \
PDF2MD_INBOX_PATH=.local/inbox \
PDF2MD_OUTBOX_PATH=.local/outbox \
PDF2MD_ENGINE_URL=http://127.0.0.1:5001 \
  uvicorn pdf2md.main:app --reload --port 8080
```

Only fidelity and isolation need the real thing: conversion quality has to be measured
against the real engine (`ops/measure-fidelity.py`), and the network properties have to
be measured against the deployed stack (`ops/verify-offline.sh`, `ops/verify-lan-only.sh`).

## Two things worth knowing before changing anything

**The engine serves each result exactly once.** `DOCLING_SERVE_SINGLE_USE_RESULTS`
defaults to true, so `GET /v1/result/{task_id}` is called once per job and its payload is
written to the outbox and committed in the same step. Any failure after the fetch marks
the job failed with a reason naming the lost result — it is never left running, because a
second fetch would return nothing. That is `Dispatcher.fetch_and_persist`, and it is the
most failure-sensitive code in the repository.

**The two-network split is load-bearing.** Putting everything on one `internal` network
looks tidier and breaks published ports entirely — measured, not assumed. `core` is
internal so the engine has no route out; `edge` is a bridge with masquerade disabled so
the published port works while egress blackholes. Change `networks:` and you have changed
the security posture of the stack; re-verify with the two scripts in `ops/`.
