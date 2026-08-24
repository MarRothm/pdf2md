# Tasks: Images as files, not as Markdown

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Directory**: `specs/003-extract-images`

**Tests are included.** This repository keeps unit, contract, and integration tests beside
the code under three pytest markers, and every prior feature's tasks did the same.

## Format: `[ID] [P?] [Story] Description`

- **[P]** — different files, no dependency on an incomplete task; safe to run in parallel
- **[US1] / [US2] / [US3]** — the user story the task serves
- Paths are repository-relative

## Path Conventions

Service code in `src/pdf2md/`, tests in `tests/{unit,contract,integration}/`, stack
definition in `deploy/docker-compose.yml`, contracts under `specs/*/contracts/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Confirm the baseline is green before touching anything: `pytest -q && ruff check src tests && ruff format --check src tests`
- [X] T002 [P] Add the four settings to `src/pdf2md/config.py` — `extract_images` (bool, default true), `image_page_coverage` (float, default 0.8), `image_min_bytes` (int, default 4096), `image_max_per_document` (int, default 500) — each with the docstring rationale from research.md R7
- [X] T003 [P] Pass all four through `deploy/docker-compose.yml` as `PDF2MD_EXTRACT_IMAGES`, `PDF2MD_IMAGE_PAGE_COVERAGE`, `PDF2MD_IMAGE_MIN_BYTES`, `PDF2MD_IMAGE_MAX_PER_DOCUMENT`, so `test_every_documented_setting_can_actually_be_set` and `test_every_shipped_setting_is_in_the_stack_contract` both pass
- [X] T004 [P] Add a row per setting to `specs/001-docling-pdf2md-stack/contracts/stack.md`, with the default and the reason it has that value

---

## Phase 2: Foundational (Blocking Prerequisites)

**T005 ran on 2026-08-24 and passed — at the second attempt.** The first request asked for
`image_export_mode=placeholder` and every picture came back with no image data; under
`embedded` a running conversion put 218 pictures into the scratch area within the first few
parts. The gate did its job: it caught a wrong reading of the engine before the feature was
trusted, just later than it should have. See research.md R3.

- [X] T005 Run quickstart V7 on the Mac mini: convert one illustrated document and confirm `json_content.pictures[]` is non-empty, each entry carries `image.uri` beginning `data:`, `image.mimetype`, `prov[0].page_no` and `prov[0].bbox`, and that `pages[n].size` exists for that page. **If any of it is absent, stop and revisit research.md R2/R3** — the fallback is placeholder-only conversion, not a workaround in the client. Record what was observed in `specs/003-extract-images/research.md` under R3
- [X] T006 In `src/pdf2md/docling_client.py`, send `to_formats=["md","json"]`, `image_export_mode` (`placeholder`), and `include_images=true` on submit, per `contracts/docling-serve-images.md`; send `image_export_mode=placeholder` and omit the JSON format when `extract_images` is false, so the Markdown never carries picture data either way (FR-001, FR-010)
- [X] T007 In `src/pdf2md/docling_client.py`, carry `json_content` through on `ConversionResult` without parsing it, and wire `Settings` into `DoclingClient` in `src/pdf2md/main.py` as the OCR settings already are
- [X] T008 [P] In `tests/stubs/docling_stub.py`, let `TaskBehavior` carry pictures — a list of (data URI, mimetype, page number, bbox, page size) — and return them as `json_content.pictures[]` with `pages{}`, emitting one `<!-- image -->` per picture in `md_content`
- [X] T009 [P] Create migration `005_extracted_images` in `src/pdf2md/db.py`: the `extracted_image` table from data-model.md keyed on `image_filename`, indexed on `content_hash`, plus `image_count` on `source_document`
- [X] T010 [P] Add `image_filename(original_name, content_hash, ordinal, mimetype)` to `src/pdf2md/naming.py`, producing `{slug}--{hash12}--img{NNN}.{ext}` (research R5), and reject a mimetype that is not a known image type rather than inventing an extension

**Checkpoint**: the engine is asked for what we need, the stub can return it, and the schema exists. Nothing writes a file yet.

---

## Phase 3: User Story 1 — Markdown that is text, and only text (Priority: P1) 🎯 MVP

**Goal**: Markdown containing references instead of pictures, for a document converted whole.

**Independent test**: convert a document with one figure; the Markdown has a reference where the figure was, and no picture data anywhere.

### Tests for User Story 1

- [X] T011 [P] [US1] Create `tests/unit/test_images.py` covering the pure functions: a `data:` URI decoded to bytes and mimetype; a non-`data:` URI rejected; bbox-to-page-area at 0.79 and 0.81 of `IMAGE_PAGE_COVERAGE` (both sides of the boundary, FR-004); a picture under `IMAGE_MIN_BYTES`; the `IMAGE_MAX_PER_DOCUMENT` ceiling; a picture with no `prov`; and a placeholder/picture count mismatch reported rather than reconciled
- [X] T012 [P] [US1] Extend `tests/contract/test_docling_client.py` with the three new submission fields from `contracts/docling-serve-images.md`, and with the `extract_images=false` case asserting `placeholder` is still sent
- [X] T013 [P] [US1] Create `tests/integration/test_images.py` with quickstart V1 and V2: no `data:` URI and no base64 run survives in the written Markdown, and every `![](…)` names a file that exists in the outbox — read back from disk, not from the string that was written

### Implementation for User Story 1

- [X] T014 [US1] Create `src/pdf2md/images.py` with no I/O: `decode_data_uri`, `is_page_sized(bbox, page_size, coverage)`, and `plan_extraction(pictures, settings)` returning, per picture, whether it is extracted, skipped, or page-sized (FR-004, FR-005)
- [X] T015 [US1] In `src/pdf2md/images.py`, add `rewrite_placeholders(markdown, decisions, filenames)` implementing the three cases of FR-006: a reference for an extracted picture, **nothing** for a page-sized one, and a short note for one skipped as too small or past the ceiling
- [X] T016 [US1] In `src/pdf2md/dispatcher.py`, extract images in `fetch_and_persist` for the whole-document path: plan from `json_content`, write the kept images to the outbox via `Storage`, rewrite the Markdown, and record `extracted_image` rows and `image_count` inside the same transaction that records the outputs — the fetch-and-persist step must stay one step (feature 001 research R3)
- [X] T017 [US1] In `src/pdf2md/storage.py`, add `write_outbox_image_atomic(filename, payload)` alongside the Markdown writer, using the same temp-file-and-rename discipline
- [X] T018 [US1] In `src/pdf2md/db.py`, add `record_images` to the same transaction as `record_outputs_and_finish`, and extend supersession so images the new conversion did not rewrite are deleted with the Markdown files that are (data-model.md)
- [X] T019 [US1] Write images **before** the Markdown that references them, so a failure mid-document leaves unreferenced files rather than references to nothing (plan.md, Risks), and log `images=` on the existing `job_succeeded` line

**Checkpoint**: FR-001 is delivered — the requirement AnythingLLM actually depends on. A split document still converts, keeping its placeholders.

---

## Phase 4: User Story 2 — The pictures themselves, alongside the document (Priority: P2)

**Goal**: every reference resolves, including across the split-and-join boundary and inside the archive.

**Independent test**: convert a document with figures across several parts; every reference in the joined Markdown opens a file showing the expected figure.

### Tests for User Story 2

- [X] T020 [P] [US2] Extend `tests/integration/test_images.py` with quickstart V3 and V4: a page-sized picture leaves no file and **nothing** in the Markdown, while a picture at 79% coverage on the same page is extracted; a too-small picture and one past the ceiling each leave a note
- [X] T021 [P] [US2] Extend `tests/integration/test_images.py` with quickstart V5: a document split into parts, each with pictures, produces one ordinal sequence in document order — not one restarting per part — and every reference resolves after the join
- [X] T022 [P] [US2] Extend `tests/integration/test_section_output.py`: a reference resolves from a section file, and the archive from `GET /api/jobs/{job_id}/markdown.zip` contains the images with the references still resolving inside it (FR-009)

### Implementation for User Story 2

- [X] T023 [US2] In `src/pdf2md/storage.py`, add the part image scratch path `{content_hash}--part{ordinal:03d}--img{NNN}.{ext}` on the inbox volume, and extend `delete_part_files` to sweep it (research R6)
- [X] T024 [US2] In `src/pdf2md/dispatcher.py`, write a part's images to the scratch area in `_fetch_part`, keeping the part's Markdown placeholders intact in the database
- [X] T025 [US2] In `src/pdf2md/dispatcher.py`, assign ordinals at join time in document order across all parts, move the scratch images into the outbox under their final names, and rewrite the joined Markdown's placeholders (research R6). A part whose images are missing from the scratch area is a gap to report, not a silent skip
- [X] T026 [US2] In `src/pdf2md/api/jobs.py`, include the document's images in `markdown.zip`, and set `download_all_url` when the document produced more than one file **of any kind** (`contracts/web-api-images.md`)

**Checkpoint**: references resolve everywhere a reader can encounter them — outbox, section file, archive.

---

## Phase 5: User Story 3 — Removing a document removes its pictures (Priority: P3)

**Goal**: images obey the lifecycle rules Markdown already obeys.

**Independent test**: convert an illustrated document, delete it, and confirm none of its files remain and nothing else was touched.

### Tests for User Story 3

- [X] T027 [P] [US3] Extend `tests/integration/test_images.py` with quickstart V6: deletion removes every image file and row; re-conversion leaves no orphans from the first run
- [X] T028 [P] [US3] Extend `tests/contract/test_deletion.py`: the confirmation names image files before the operator commits, and an image that could not be removed appears in `kept_files` exactly as an unremovable Markdown file does (FR-008)

### Implementation for User Story 3

- [X] T029 [US3] In `src/pdf2md/deletion.py`, remove `extracted_image` rows and their files with the document's Markdown, reporting removed and kept files together — only names recorded in the database, never a scan of the outbox
- [X] T030 [US3] In `src/pdf2md/db.py`, cascade `extracted_image` on document deletion and include image files in the counted entries the confirmation is built from
- [X] T031 [P] [US3] In `src/pdf2md/models.py` and `src/pdf2md/api/jobs.py`, add `image_count` to the job summary and detail payloads (`contracts/web-api-images.md`)
- [X] T032 [P] [US3] In `src/pdf2md/static/app.js`, show the image count in the detail view's facts, so a document that extracted nothing is distinguishable from one that had nothing to extract (FR-013)

**Checkpoint**: all three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T033 [P] Update `specs/001-docling-pdf2md-stack/data-model.md` and `contracts/web-api.md` with the `extracted_image` entity and `image_count`, keeping the two existing drift guards passing
- [X] T034 [P] Add the recognition-and-pictures section to `deploy/README.md`: what is extracted, what is not, the four settings, and that turning extraction off does **not** restore pictures inside the Markdown
- [ ] T035 Run quickstart V8 on the Mac mini: a fully scanned document produces no image files (SC-007). If page images are extracted, measure `IMAGE_PAGE_COVERAGE` against real documents rather than adjusting it by feel
- [ ] T036 Run quickstart V9: import a converted illustrated document into AnythingLLM and confirm it ingests without the failures embedded pictures cause (SC-009) — the operator's actual reason for the feature
- [ ] T037 Run quickstart V10: convert the same document with extraction on and off, compare the `job_succeeded` timings against SC-008's 25%, and **write both figures into the measurements table in `deploy/README.md`** — the table that has read *not yet measured* since the first release
- [X] T038 Run `ruff check src tests && ruff format --check src tests` and the full suite; bump `src/pdf2md/__init__.py` for the release

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** — no dependencies
- **Phase 2 (Foundational)** — needs Phase 1. **T005 blocks T014 onwards absolutely**: it is the only task that checks the engine actually behaves as the design assumes
- **Phase 3 (US1)** — needs Phase 2 complete
- **Phase 4 (US2)** — needs Phase 3 (reuses `images.py` and the outbox writer)
- **Phase 5 (US3)** — needs Phase 3 (needs rows to delete); independent of Phase 4
- **Phase 6** — needs whichever stories shipped

### User Story Dependencies

- **US1** stands alone and is the MVP.
- **US2** extends US1 to split documents, section files, and the archive.
- **US3** extends US1 to the lifecycle. US2 and US3 do not depend on each other and can proceed in parallel once US1 is done.

### Within Each User Story

Tests first where they pin behaviour that is easy to get subtly wrong — the geometry rule and the three placeholder cases especially. Then the pure module, then the dispatcher wiring, then storage and database.

### Parallel Opportunities

- T002, T003, T004 (setup) — three different files
- T008, T009, T010 (stub, migration, naming) — independent of each other
- T011, T012, T013 (the three test files for US1)
- T020, T021, T022 (US2 tests) and T027, T028 (US3 tests)
- T031, T032 (payload and page) once rows exist
- T033, T034 (documentation)

---

## Parallel Example: User Story 1

```
# after T010, three test files at once:
T011  tests/unit/test_images.py
T012  tests/contract/test_docling_client.py
T013  tests/integration/test_images.py

# then serially, because they touch the same call path:
T014 → T015 → T016 → T017 → T018 → T019
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

Phases 1–3, then stop and deploy. That delivers Markdown with no picture data — the thing the
knowledge base is failing on today — and the images are written and referenced for a
whole-document conversion. A split document keeps placeholders until Phase 4, which is a
visible, honest, temporary state rather than a broken one.

### Incremental Delivery

1. Phases 1–2 alone are safe to ship: they change what is asked of the engine and add unused pure functions. Markdown loses its embedded pictures immediately, which is already a fix.
2. Phase 3 → release. Convert one illustrated document and read it.
3. Phase 4 → release. Re-convert the 2038-page document, which is split and therefore not covered before this point.
4. Phase 5 → release. Then Phase 6's measurements.

### Notes

- **T005 is not a formality.** Two of this repository's engine assumptions have been wrong this month, both discovered days later through the damage they caused. It costs one conversion.
- Images are written before the Markdown that references them, everywhere — the failure direction that leaves recoverable state.
- A count mismatch between placeholders and pictures is reported, never reconciled. A reference pointing at the wrong figure is worse than no reference at all.
