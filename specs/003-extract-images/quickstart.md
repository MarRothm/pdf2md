# Quickstart: images as files, not as Markdown

Validation for [spec.md](spec.md). Scenarios V1–V6 run against the stub engine and are
automated; V7–V10 need the real engine on the Mac mini and a real document, because the
thing being validated is what docling actually returns.

**Do not treat V7 as optional.** Every scenario before it is a test of our own code against
our own stub. The feature's central assumption — that pictures arrive in `json_content` with
usable geometry — is an assumption about a third party, and this repository has been wrong
about that twice this month.

---

## Prerequisites

```bash
pip install -e '.[dev]'
pytest -q                 # baseline green before starting
```

For V7 onwards: the stack deployed on the Mac mini, and a PDF containing **both** a
part-page figure and at least one fully scanned page. The InterRisk Vertragswerk qualifies.

---

## V1 — Markdown carries no picture data

```bash
pytest tests/integration/test_images.py -k markdown_has_no_picture_data -q
```

**Expect**: a document whose engine result contains pictures produces Markdown with no
`data:` URI and no base64 run anywhere in it, and a reference where each extracted picture
stood. This is FR-001, the requirement the knowledge base actually depends on.

## V2 — References resolve

```bash
pytest tests/integration/test_images.py -k references_resolve -q
```

**Expect**: every `![](…)` in the written Markdown names a file that exists in the outbox
(FR-003). The test reads the file back off disk rather than trusting the string it wrote.

## V3 — A page-sized image is not extracted

```bash
pytest tests/integration/test_images.py -k page_sized -q
```

**Expect**: a picture whose bbox covers ≥ `IMAGE_PAGE_COVERAGE` of its page produces no
file and leaves **nothing** in the Markdown — not a note (FR-004, FR-006). A picture at 79%
coverage on the same page is extracted, so the boundary itself is exercised.

## V4 — Skipped pictures say so; page-sized ones do not

```bash
pytest tests/integration/test_images.py -k skipped -q
```

**Expect**: a picture under `IMAGE_MIN_BYTES`, and one past `IMAGE_MAX_PER_DOCUMENT`, each
leave a note in the Markdown; neither leaves data. The distinction from V3 is the whole
point of FR-006.

## V5 — Split documents number their pictures once

```bash
pytest tests/integration/test_images.py -k across_parts -q
```

**Expect**: a document split into parts, each part carrying pictures, produces one ordinal
sequence in document order — not a sequence restarting per part — and every reference in
the joined Markdown resolves (research R6).

## V6 — Deletion and re-conversion take the images with them

```bash
pytest tests/integration/test_images.py -k lifecycle -q
pytest tests/integration/test_delete_flow.py -q
```

**Expect**: deleting the document removes every image file and row and names them in the
confirmation first; converting again replaces the set with no orphans from the first run
(FR-008). Nothing belonging to another document is touched.

---

## V7 — The engine actually returns what we think it does

**This is the one that could invalidate the design.** On the Mac mini, with the stack
running:

Portainer → Containers → `web` → Console:

```
python3 - <<'PY'
import json, urllib.request, os
# submit a small illustrated PDF already in the inbox and print the shape of what comes back
PY
```

Or more simply, convert one illustrated document through the page and then check the log
line `job_succeeded` for `images=`.

**Expect**: `json_content.pictures[]` is non-empty for a document with figures, each entry
has `image.uri` beginning `data:`, `image.mimetype`, and `prov[0].page_no` and `bbox`; and
`pages` carries a `size` for that page.

**Result, 2026-08-24**: passed under `image_export_mode=embedded` — 218 pictures written to
the scratch area from the first few parts of the 2038-page contract. It **failed** under
`placeholder`, which is what the feature originally shipped with: the pictures were returned
with no image data and the Markdown filled with *not extracted* notes.

**If any of that is absent**, stop. The design in research R3 does not hold, and the
alternatives — the zip target and its loss of `errors`/`status`/`processing_time` (R2) — are
a different feature with a different cost. Do not work around it in the client.

## V8 — A real scanned page produces no image file

Convert a document that is entirely scanned pages.

**Expect**: no image files at all, and Markdown identical in substance to what the same
document produces today (SC-007). If page images *are* extracted, `IMAGE_PAGE_COVERAGE` is
wrong for real documents and needs measuring, not adjusting by feel.

## V9 — The knowledge base ingests it

Import a converted illustrated document into AnythingLLM.

**Expect**: it ingests without the failures embedded pictures currently cause (SC-009).
This is the operator's actual reason for the feature and the only scenario that tests it.

## V10 — The cost

Convert the same illustrated document with `PDF2MD_EXTRACT_IMAGES=true` and `=false`, and
compare the times in the `job_succeeded` log lines.

**Expect**: no more than 25% longer with extraction on (SC-008). Record the figures in
`deploy/README.md`'s measurements table — the table that has said *not yet measured* since
the first release, and whose emptiness is what let three wrong assumptions stand.

---

## Rollback

`PDF2MD_EXTRACT_IMAGES=false` is a stack variable: set it, redeploy, and the service returns
to Markdown with no picture data and no image files. Note that this is **not** the old
behaviour — the old behaviour put pictures *into* the Markdown, and nothing about this
feature offers that back, because the knowledge base cannot ingest it.
