# Engine contract: pictures

Extends [feature 001's engine contract](../../001-docling-pdf2md-stack/contracts/docling-serve.md).
Verified against the pinned engine — `docling-serve v1.18.0` → `docling 2.93.0` — with the
source location for each claim, because two of this repository's engine assumptions have
already turned out to be wrong.

## What changes in the submission

`POST /v1/convert/file/async`, same multipart form, three fields added:

| Field | Value | Source of truth |
|---|---|---|
| `to_formats` | `pdf` → **`md` and `json`** | The pictures are in the JSON; the Markdown is still the output |
| `image_export_mode` | **`embedded`** when extracting, `placeholder` when not | The two exports are made from **one copy** (`results.py`: `_make_copy_with_refmode`), so this decides whether the structure carries picture bytes at all. `placeholder` leaves the document untouched and its pictures may hold nothing; `embedded` calls `_with_embedded_pictures()`, which is what creates the data URIs. Corrected after `placeholder` shipped and produced a document of *not extracted* notes |
| `include_images` | `true` (the default, sent explicitly) | `options.py:493`. Without it the JSON carries no picture bytes |

`images_scale` is left at its default of `2.0` (`options.py:504`).

**`target_type` stays `inbody`.** The zip target exists and returns the image files, but
`response_preparation.py:44-52` returns bare bytes for a `ZipArchiveResult` — no `status`,
no `errors`, no `processing_time`. Those are the entire input to the failure messages
(FR-011), the `partial_success` distinction, and the detail view. See research.md R2.

## What comes back

`GET /v1/result/{task_id}` — unchanged in shape. Still one fetch, still single-use, still
JSON. `document.md_content` now carries `<!-- image -->` placeholders instead of base64, and
`document.json_content` is a DoclingDocument:

```jsonc
{
  "pictures": [
    {
      "image": { "mimetype": "image/png", "dpi": 144, "size": {...}, "uri": "data:image/png;base64,…" },
      "prov": [ { "page_no": 12, "bbox": { "l": 72, "t": 640, "r": 520, "b": 400 }, ... } ]
    }
  ],
  "pages": { "12": { "size": { "width": 595, "height": 842 }, "page_no": 12 } }
}
```

| Field | Confirmed at |
|---|---|
| `pictures[]` | `docling_core/types/doc/document.py:206` |
| `pictures[].image.uri`, `.mimetype` | `docling_core/types/doc/common/reference.py:86-89` |
| `pictures[].prov[].page_no`, `.bbox` | `reference.py:190-191` |
| `pages[n].size` | `reference.py:195-202` |

## Rules the client applies

1. **Order.** `pictures[]` is in document order, and so are the image markers in
   `md_content` — `<!-- image -->` under `placeholder`, `![](data:…)` under `embedded`.
   Both forms are matched, in position order, because the mode is the engine's to change
   and a mismatch would empty the output silently. The *n*th marker belongs to the *n*th
   picture. A mismatch in count is
   a failure to report, not a mismatch to paper over — the reference would point at the
   wrong figure, which is worse than no reference.
2. **Bytes from either side.** A picture's data is taken from `pictures[].image.uri` when
   it is there and from the Markdown's own inline URI when it is not. Which one carries it
   depends on the export mode, and that is not a property this service should depend on.
3. **Page-sized.** `bbox` area ÷ `pages[prov.page_no].size` area ≥ `IMAGE_PAGE_COVERAGE`
   means the picture is the page: no file, and the placeholder is removed (FR-004, FR-006).
4. **Furniture.** A picture whose box lies entirely within `IMAGE_HEADER_BAND` of the page
   top or `IMAGE_FOOTER_BAND` of its bottom is the page's furniture and is not extracted
   (FR-014). `prov[].coord_origin` decides which way up the coordinates run — `TOPLEFT` or
   `BOTTOMLEFT` — and reading it wrongly would protect the wrong end of the page while
   appearing to work.
5. **Floor and ceiling.** Below `IMAGE_MIN_BYTES`, or past `IMAGE_MAX_PER_DOCUMENT`, the
   picture is skipped and the placeholder becomes a note (FR-005, FR-006).
6. **A picture with no `image`, no `prov`, or an unreadable URI** is skipped and counted,
   never guessed at. The document still finishes (FR-012).
7. **Nothing is fetched from a URI that is not a `data:` URI.** A picture referencing an
   http address is skipped: the engine has no route out and neither has this service
   (FR-011, feature 001 FR-021).
