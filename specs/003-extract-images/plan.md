# Implementation Plan: Images as files, not as Markdown

**Feature**: [spec.md](spec.md) · **Directory**: `specs/003-extract-images`

**Created**: 2026-08-23 · **Status**: Design complete, ready for `/speckit-tasks`

## Summary

The Markdown this service produces currently contains every picture docling finds, base64
inside the file, because `image_export_mode` defaults to `embedded` and the deployment has
never sent the field. AnythingLLM cannot ingest a document in that form, which makes this a
correctness problem rather than a tidiness one.

The fix is small at the engine boundary and unglamorous everywhere else: ask for
`placeholder` mode and for the JSON alongside the Markdown, take the pictures out of
`json_content` where they arrive with their page and bounding box, write the ones that are
figures to the outbox, and rewrite the placeholders as references. Everything after that is
making images obey the rules Markdown files already obey — naming, supersession, deletion,
archiving, counting.

**The single riskiest thing here is not code.** It is whether the engine returns pictures the
way research R3 says it does. That is verified against the pinned source, not against a
running engine, and quickstart V7 exists to close the gap before any of this is trusted.

## Technical Context

| | |
|---|---|
| **Language** | Python 3.12, as the rest of the service |
| **New dependencies** | **None.** `base64`, `zipfile`, and `pathlib` are stdlib. Image bytes arrive in a response already fetched; nothing decodes, re-encodes, or resizes a picture |
| **Engine** | `docling-serve-cpu v1.18.0`, unchanged and unpinned-differently. Three request fields change; the result contract does not |
| **Storage** | New `extracted_image` table; new scratch files on the inbox volume; new files in the outbox |
| **Scale** | Bounded by `IMAGE_MAX_PER_DOCUMENT` (500). A 2038-page contract is expected to produce tens of figures, not hundreds — its scanned pages are excluded by FR-004 |
| **Constraints** | Offline (FR-021, FR-022); no picture held in memory longer than writing it takes (FR-042); one engine fetch per part, single-use (feature 001 research R3) |

No NEEDS CLARIFICATION remain. FR-004 was resolved by the operator during `/speckit-specify`
(page-sized images are not extracted) and turned into a measurable rule in research R4.

## Constitution Check

`.specify/memory/constitution.md` is still the **unfilled template** — every principle is a
placeholder, so there are no ratified gates to check against. As features 001 and 002 both
did, this plan checks itself against the gates the repository actually enforces:

| De-facto gate | Enforced by | This feature |
|---|---|---|
| The page reaches nothing but this origin | `tests/unit/test_static_assets.py` | **Pass** — image references are relative filenames; no URL, no CDN, and a picture whose URI is not a `data:` URI is skipped (engine contract, rule 5) |
| No dependency the offline image cannot carry | `Dockerfile`, CI | **Pass** — nothing added |
| Contract-first: interfaces documented before built | `specs/*/contracts/` | **Pass** — [engine](contracts/docling-serve-images.md), [web API](contracts/web-api-images.md) |
| Destructive filesystem work is narrow and deliberate | `storage.delete_outbox_file` docstring | **Pass** — images are deleted only by recorded filename, never by scanning or globbing the outbox. This is why `ExtractedImage` rows exist at all |
| One error shape, message safe to display | `main.py` handlers | **Pass** — no new error path; a picture that cannot be stored degrades the document (FR-012), it does not fail it |
| Tests beside the code, three markers | `pyproject.toml`, `tests/` | **Pass** — unit for the geometry rule, contract for the engine request and the payload, integration for the lifecycle |
| Every setting documented and actually settable | `test_compose_pins.py` (two guards) | **Pass** — four new settings, all four in the compose file and in `contracts/stack.md` |

The constitution should be ratified (`/speckit-constitution`); it is flagged in feature 002's
analysis and is not this feature's to fix.

## Project Structure

```
src/pdf2md/
├── images.py          NEW  the geometry rule, decoding, naming, placeholder rewriting
├── docling_client.py       three request fields; json_content on ConversionResult
├── dispatcher.py           write part images at fetch; move and rewrite at join
├── db.py                   extracted_image table, migration 005, supersession
├── deletion.py             images join the removal and the confirmation
├── storage.py              image paths, scratch sweep, outbox writes
├── models.py               ExtractedImage, image_count on the payloads
├── naming.py               image_filename()
└── api/jobs.py             image_count; archive includes images

specs/003-extract-images/
├── spec.md  plan.md  research.md  data-model.md  quickstart.md
├── contracts/docling-serve-images.md
├── contracts/web-api-images.md
└── checklists/requirements.md

tests/
├── unit/test_images.py            geometry, naming, placeholder rewriting
├── contract/test_docling_client.py    the three new request fields
├── integration/test_images.py     V1–V6
└── stubs/docling_stub.py          returns json_content with pictures
```

## Implementation Phases

**Phase 1 — the boundary, and nothing else.** `docling_client` sends the three fields and
carries `json_content` through on `ConversionResult`. Stub returns pictures. Nothing writes a
file yet. *Done when*: a contract test shows the request the engine will receive, and
`md_content` in the stub's reply has placeholders rather than base64.

**Phase 2 — `images.py`, pure functions, no I/O.** Decode a `data:` URI; decide page-sized
from bbox and page size; apply floor and ceiling; rewrite placeholders in order into
references, removals, and notes. *Done when*: unit tests cover the 0.8 boundary from both
sides, a count mismatch between placeholders and pictures, a missing `prov`, and a
non-`data:` URI. This phase is where the feature is actually decided, and it touches nothing.

**Phase 3 — one file, one document (User Story 1 + 2).** Whole-document path only: write
images at fetch, name them, rewrite the Markdown, record rows. *Done when*: V1, V2, V3, V4
pass. A split document is untouched and still works — it simply keeps its placeholders.

**Phase 4 — split and join (research R6).** Part images to the inbox scratch area, moved and
renumbered at join. *Done when*: V5 passes and `delete_part_files` sweeps the scratch.

**Phase 5 — the lifecycle (User Story 3).** Supersession, deletion, the confirmation, the
outcome report, the archive, `image_count` on the payloads and the page. *Done when*: V6
passes and feature 002's deletion tests still do.

**Phase 6 — the real engine.** V7 through V10 on the Mac mini, and the measurements written
into `deploy/README.md`.

Phases 1–2 are safe to ship without 3–6: they change what is asked of the engine and add
unused pure functions. Phase 3 alone delivers User Story 1, which is the requirement
AnythingLLM depends on.

## Risks and the calls made about them

**The engine may not return pictures as described.** Verified from the pinned source
(`document.py:206`, `reference.py:83-202`), not from a running engine. If V7 disproves it,
the fallback is `placeholder` mode with no extraction — FR-001 satisfied, FR-002 not — and
the zip target is a separate decision with a real cost (research R2). *Mitigation*: V7 runs
before Phase 3, not after Phase 5.

**Placeholder-to-picture alignment.** The design assumes the *n*th `<!-- image -->` is the
*n*th entry of `pictures[]`. If docling ever emits a placeholder for something not in
`pictures[]`, references would silently point at the wrong figure. *Call*: a count mismatch
is treated as a failure to report, not to reconcile — a wrong reference is worse than none.

**Coverage threshold is a guess until measured.** 0.8 is reasoned, not measured (research
R4). *Call*: shipped as a stack variable, and V8 is the measurement. Do not tune it by feel;
this repository has already paid for a number chosen that way — `PART_MAX_PAGES=100`.

**More files per document.** Images multiply outbox writes, and a partial write mid-document
becomes likelier. *Call*: images are written before the Markdown that references them, so a
failure leaves unreferenced files rather than references to nothing — the recoverable
direction, and the one FR-012 asks for.

**The outbox gets busier.** A document already produces up to 1344 Markdown files; images add
to that. *Call*: out of scope. If the operator wants fewer files, `SECTION_MIN_BYTES` is the
lever, and they have already chosen to turn sectioning off entirely.

## Complexity Tracking

One new module, one new table, one new migration, four new settings. The alternative designs
were all *more* complex or *worse*: the zip target loses every diagnostic (R2), two
submissions double engine time on a CPU-bound host (R2), pixel-size classification is
unstable across scan resolutions (R4), and a per-document subfolder would teach deletion,
archiving, and confirmation about directories for no visible gain (R5).

The one place complexity is deliberately accepted is research R6 — carrying images across the
split boundary as scratch files rather than database columns. That is more moving parts than
holding them in memory, and it is the direct lesson of FR-042.
