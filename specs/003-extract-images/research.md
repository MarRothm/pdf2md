# Research: Images as files, not as Markdown

Phase 0 for [spec.md](spec.md). Everything below was checked against the **pinned** engine —
`docling-serve v1.18.0` → `docling 2.93.0`, `docling-jobkit 1.18.1` — not against current
`main` and not from memory. This repository has lost three days this month to decisions
built on unverified engine behaviour (research.md R4 and R6 of feature 001 were both wrong),
so each finding below names the file it came from.

---

## R1. Why the Markdown contains pictures at all

**Finding**: `ConvertDocumentsOptions.image_export_mode` defaults to
**`ImageRefMode.EMBEDDED`** (`docling/datamodel/service/options.py:325-337`). This service
has never sent the field, so every picture docling identifies is base64-inlined into
`md_content`. Nothing is misbehaving — the deployment has been taking a default that is
wrong for its purpose.

Allowed values are `embedded`, `placeholder`, and `referenced`. `include_images` defaults to
`true` (`options.py:493`) and `images_scale` to `2.0` (`options.py:504`).

**Consequence beyond this feature**: the 20 MB of Markdown now heading for AnythingLLM is
text *plus* every picture in a 2038-page contract. Fixing this shrinks the output and
reduces what the join holds in memory (FR-042).

---

## R2. How the engine can return image files — and why we will not ask it to

**Decision**: **Do not use the zip target.** Keep `target=inbody` and the JSON result exactly
as it is today.

The zip target is real. `docling_serve/app.py:841-859` shows the async file endpoint
accepting `target_type` as a multipart form field (`inbody` | `zip`), and
`docling_serve/response_preparation.py:44-52` shows what comes back for a `ZipArchiveResult`:

```python
elif isinstance(task_result.result, ZipArchiveResult):
    response = Response(
        content=task_result.result.content,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="converted_docs.zip"'},
    )
```

Bare bytes. Compare the `ExportResult` branch immediately above it, which returns
`status`, `processing_time`, `timings`, and `errors`.

**Rationale for rejecting it**: those four fields are not decoration. `errors` and `status`
are the entire input to `DoclingClient.failure_reason_from`, which is how a failure becomes a
sentence the operator can act on (FR-011); `status` is how `partial_success` is
distinguished from success; `processing_time` is on the detail view; and the page count that
`is_suspect_yield` needs (FR-029) travels in the same payload. Switching to zip would trade
a feature about pictures for the loss of every diagnostic this service has — in a codebase
that spent this week learning what unreported failure costs.

**Alternatives considered**:

- *Two submissions per part, one `inbody` and one `zip`*: doubles engine time on a CPU-only
  host that is already the bottleneck. Rejected.
- *`referenced` mode with `inbody`*: the Markdown would reference paths inside the engine's
  own scratch directory, which is discarded when the task is cleaned up. Every reference
  would be broken. Rejected — this is the trap the feature would have fallen into had the
  spec's assumption been taken at face value.

---

## R3. Where the pictures actually come from

**Decision**: request `to_formats=["md", "json"]` with
`image_export_mode=placeholder`. Take the Markdown from `md_content` and the pictures from
`json_content`.

**Verified structure** (`docling_core/types/doc/document.py:206,213` and
`docling_core/types/doc/common/reference.py:83-202`):

| What we need | Where it is | Confirmed at |
|---|---|---|
| Every picture | `json_content.pictures[]` | `document.py:206` |
| The image bytes | `pictures[].image.uri` — a `data:` URI when embedded | `reference.py:89` |
| Its type | `pictures[].image.mimetype` | `reference.py:86` |
| Its page and box | `pictures[].prov[].page_no`, `.bbox` | `reference.py:190-191` |
| The page's size | `json_content.pages[page_no].size` | `reference.py:195-202` |

**Rationale**: this is one fetch, of one JSON body, over the interface already in use — so
`DOCLING_SERVE_SINGLE_USE_RESULTS` and the fetch-and-persist-in-one-step rule (feature 001
research R3) are untouched. `placeholder` keeps picture data out of `md_content`
unconditionally, which is what FR-001 asks for; the pictures then arrive separately, with
enough geometry to decide which of them are figures.

**Cost**: `json_content` is a full DoclingDocument and is larger than the Markdown. It is
read once per part, converted to files, and dropped — it is never stored. Peak memory per
part is comparable to today's, where the same picture bytes arrive base64-inlined in
`md_content` instead.

---

## R4. Telling a figure from a scanned page (FR-004)

**Decision**: a picture is **page-sized**, and therefore not extracted, when its bounding box
covers at least `IMAGE_PAGE_COVERAGE` (default **0.8**) of its page's area. Everything else
is a figure.

**Rationale**: the spec's rule is geometric, and R3 gives us the geometry exactly —
`bbox` against `pages[page_no].size`. No heuristic on pixel dimensions, no guessing from
aspect ratio, no dependence on `images_scale`. A scanned page's picture covers essentially
the whole page; a figure filling most of a page still leaves margins, headers, and a caption,
which is why the threshold is 0.8 rather than something tighter.

**Alternatives considered**:

- *Pixel-size threshold*: depends on `images_scale` and on the source's DPI, so the same
  document scanned at two resolutions would be classified differently. Rejected.
- *Ask docling to classify*: `PictureClassificationData` exists, but the classifier
  distinguishes charts from logos, not "page" from "figure". Rejected — wrong question.
- *Count pictures per page and treat a lone picture as the page*: fails on a page whose only
  content is a genuine figure. Rejected.

---

## R5. Where images live and what they are called

**Decision**: flat in the outbox, beside the Markdown, named
`{slug}--{hash12}--img{NNN}.{ext}` — the same prefix as the document's Markdown, an ordinal
in document order, and the extension from the picture's mimetype.

**Rationale**: every mechanism that already exists keys on a recorded filename in one flat
folder — `storage.delete_outbox_file` refuses to scan and only removes names recorded in the
database, the deletion confirmation lists names, the archive collects names. A subfolder per
document would mean teaching all of that about directories, for no gain the operator can
see: they open the outbox in Finder either way. Sharing the `{slug}--{hash12}` prefix means a
document's files sort together, which is how the operator finds them.

A reference from `{slug}--{hash12}--003-installation.md` to `{slug}--{hash12}--img007.png` is
a plain relative name in the same directory, so it resolves from a section file (FR-033) and
from the whole-document file alike, and it still resolves inside the archive (FR-009).

**Ordinals are assigned at join time, in document order** — not per part. A part knows only
its own pictures, and numbering within parts would restart at one for every 40 pages.

---

## R6. Carrying images across the split-and-join boundary

**Decision**: a part writes its pictures to the **inbox** scratch area as
`{hash}--part{ordinal:03d}--img{NNN}.{ext}` at fetch time, alongside the part PDFs already
there. The join moves them into the outbox under their final document-order names and
rewrites the placeholders in the joined Markdown.

**Rationale**: a part's Markdown is held in the database because the engine serves each
result exactly once and the fetch must be durable in one step (feature 001 research R3). The
same argument applies to its pictures — but they are binary and unbounded, and this service
was killed a week ago for holding one document's text in memory (FR-042). Files on the inbox
volume are the right shape: durable across a restart, already swept by `delete_part_files`,
and never loaded whole.

**Consequence**: `Storage.delete_part_files` must sweep the image scratch files too, and the
crash-loop protection of FR-042 now guards a document that produces too many files as well as
one too large to join.

---

## R7. The knobs, and their defaults

| Setting | Default | Why |
|---|---|---|
| `PDF2MD_EXTRACT_IMAGES` | `true` | The request is to change the behaviour, not to offer it (spec Assumptions). `false` sends `image_export_mode=placeholder` and writes no files — Markdown with no picture data either way, which is the part of FR-001 that must never regress |
| `PDF2MD_IMAGE_PAGE_COVERAGE` | `0.8` | Above this fraction of its page, a picture is the page (R4, FR-004) |
| `PDF2MD_IMAGE_MIN_BYTES` | `4096` | Below this a picture is a rule, a bullet, or a spacer (FR-005) |
| `PDF2MD_IMAGE_MAX_PER_DOCUMENT` | `500` | A safety net for a document that defeats the coverage rule. Past it, pictures are skipped and the Markdown says so (FR-005, FR-006) |

All four are stack variables, documented in `contracts/stack.md`, and covered by the two
existing guards — every setting in the compose file must appear in the contract, and every
setting named in the operator's guide must actually reach the container.

---

## R8. What this does *not* change

- **Recognition** (FR-039). Whether a page's text is read is untouched; this is about what
  happens to the picture afterwards.
- **The result contract**. One submission, one poll, one fetch, JSON, single-use.
- **Isolation** (FR-021, FR-022). No new dependency, no new network path: `base64` and
  `pathlib` are stdlib, and the picture bytes arrive in the response we already fetch.
- **Suspect-yield** (FR-029). Character counts are unaffected by removing base64 from the
  Markdown, because base64 was never text the check should have been counting — if anything
  the check becomes more honest.
