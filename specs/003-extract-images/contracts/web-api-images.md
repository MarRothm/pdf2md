# Web API: what the page learns about pictures

Extends [feature 001's web API contract](../../001-docling-pdf2md-stack/contracts/web-api.md).

## Job payload additions

```jsonc
{
  "image_count": 7,          // pictures extracted for this document
  "output_file_count": 4,    // existing: Markdown files only
  "download_all_url": "/api/jobs/{job_id}/markdown.zip"
}
```

`image_count` is on both the summary and the detail. It exists to separate *a document with
no pictures* from *a document whose pictures were not extracted* (FR-013) — without it both
are an outbox with no image files in it.

## `GET /api/jobs/{job_id}/markdown.zip`

Unchanged endpoint, wider contents: the archive now carries the document's images alongside
its Markdown, flat, under the same names they have in the outbox. References inside the
archive therefore resolve exactly as they do in the outbox (FR-009, FR-003).

`download_all_url` is set when the document produced more than one file **of any kind** —
so a single Markdown file with three figures is now offered as an archive, where before it
was offered as one file.

## Detail view

`outputs` and `document_outputs` continue to list Markdown files only. The images are
reported by `image_count` rather than enumerated: a document may have hundreds, and the
detail view is read by a person deciding what to do next, not an inventory.

## Deletion

`DELETE /api/jobs/{job_id}` — unchanged shape. The confirmation payload's file list and the
outcome's `removed_files` / `kept_files` now include image files (FR-008, feature 002
FR-016 through FR-018). An image that could not be removed is reported exactly as an
unremovable Markdown file is, and for the same reason: the operator has to be able to
reconcile the folder afterwards.

## Health

Unchanged. Image extraction adds no state that can stall, and nothing about it belongs in a
signal whose job is to say whether work is moving (FR-041).
