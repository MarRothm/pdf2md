# Data Model: Images as files, not as Markdown

Extends the model in [feature 001](../001-docling-pdf2md-stack/data-model.md). Only what
changes is written here.

---

## ExtractedImage (new)

One picture taken from a source document and written to the outbox. The row is what makes
the file findable, replaceable, and deletable — a file on disk with no row is exactly the
orphan feature 002 exists to prevent, because `storage.delete_outbox_file` only ever removes
names the database recorded and never scans the folder.

| Field | Type | Notes |
|---|---|---|
| `image_filename` | TEXT PK | `{slug}--{hash12}--img{NNN}.{ext}` (research R5). Primary key, so a re-conversion overwrites the row in place exactly as `markdown_output` does |
| `content_hash` | TEXT FK → `source_document.content_hash` | The document it belongs to. Deletion and supersession both key on this |
| `job_id` | TEXT FK → `conversion_job.id` | The conversion that wrote the current file |
| `ordinal` | INTEGER | 1-based, in document order across every part (research R6). What `img{NNN}` is derived from |
| `page_no` | INTEGER NULL | Source page, for the operator tracing a figure back to the PDF |
| `mimetype` | TEXT | As reported by the engine; decides the file extension |
| `bytes` | INTEGER | Size of the written file |
| `written_at` | TEXT | |

Indexed on `content_hash`, like `markdown_output`.

**Not stored**: the image bytes. They are written to the outbox and never held in the
database — the argument that puts a part's Markdown in a column (single-use results, feature
001 research R3) does not extend to binary of unbounded size, and this service has already
been killed once for holding a document whole (FR-042).

---

## MarkdownOutput (unchanged shape, new relationship)

A Markdown file may now reference `ExtractedImage` rows of the same `content_hash`. The
relationship is by convention — a relative filename inside the text — not by a foreign key,
because the reference lives in the file's bytes and must keep working when the file is copied
out of the outbox or unpacked from an archive.

**Consequence for supersession**: `persist_markdown` computes superseded Markdown as
*previously recorded files minus the new set*. Images need the identical treatment against
`extracted_image`, or a document whose second conversion finds fewer pictures leaves the
extra files behind for ever.

---

## SourceDocument (one new field)

| Field | Type | Notes |
|---|---|---|
| `image_count` | INTEGER NOT NULL DEFAULT 0 | Pictures the current conversion extracted. Distinguishes *a document with no pictures* from *a document whose pictures were not extracted* (FR-013), which are otherwise the same empty outbox |

---

## ConversionPart (no schema change, new scratch files)

A part's pictures are written to the inbox volume as
`{content_hash}--part{ordinal:03d}--img{NNN}.{ext}` when its result is fetched, and moved
into the outbox at join time (research R6). They are scratch, exactly like the part PDFs
beside them, and `Storage.delete_part_files` sweeps both.

The part's own row is unchanged: `markdown` still holds its text, now with placeholders where
its pictures were.

---

## Lifecycle

| Moment | Images |
|---|---|
| Part result fetched | Written to the inbox scratch area; the part's Markdown keeps its placeholders |
| Job joined | Moved to the outbox in document order, rows written, placeholders rewritten as references, `image_count` set |
| Job re-converted | New set written; rows and files of the previous set that the new one does not replace are superseded and deleted (research.md R13 of feature 001) |
| Document deleted | Every row and file removed with the Markdown, named in the confirmation beforehand and in the outcome report if one could not be removed (FR-008, feature 002 FR-016/FR-017/FR-018) |
| History pruned | **Nothing happens.** Pruning never touches the outbox, and that holds for images exactly as it holds for Markdown |
| Job fails | Scratch images are swept with the part files; nothing reaches the outbox |

---

## What the Markdown carries

Three cases, from FR-006, and the distinction matters enough to be part of the model:

| Case | In the Markdown |
|---|---|
| Picture extracted | `![](slug--hash12--img007.png)` at the picture's position |
| Page-sized image (FR-004) | Nothing at all — the page is its recognised text, and a marker on every page of a scan is noise in the file and in the knowledge base |
| Skipped: too small, or past the ceiling | A short note that a picture was there, because that is information the operator would otherwise lose |

In no case does picture data remain (FR-001). That is the invariant the whole feature exists
for: the knowledge base cannot ingest a document that contains it.
