"""Pictures out of the Markdown and into files (feature 003).

The engine is asked for `placeholder` mode, so `md_content` arrives with `<!-- image -->`
where each picture stood and no picture data anywhere — that alone is what the knowledge
base needs, because it cannot ingest a document with pictures inside it. The pictures
themselves arrive separately in the document structure, each with the page it sat on and
the box it occupied, which is what lets a figure be told from a scanned page.

Everything here is pure. No engine, no filesystem, no settings object: bytes and geometry
in, decisions out. The dispatcher does the writing.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from pdf2md.naming import IMAGE_EXTENSIONS

PLACEHOLDER = "<!-- image -->"
"""What `image_export_mode=placeholder` leaves behind (contracts/docling-serve-images.md)."""

INLINE_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(data:[^)\s]+)\s*\)")
"""What `embedded` mode leaves instead: the picture itself, inline in the Markdown.

Both forms are handled. The engine's two modes are not independent of each other the way
this feature first assumed — `json_content` is the *same copy* the Markdown is made from,
so asking for `placeholder` can leave the structure with no picture bytes in it at all.
Matching either token means the rewrite works whichever mode the engine was asked for, and
a future change of its default cannot quietly empty the output."""


def image_tokens(markdown: str) -> list[tuple[int, int, str | None]]:
    """Every place a picture stands in the Markdown: (start, end, its data URI if inline).

    In document order, which is the order `pictures[]` is in — that correspondence is the
    only thing tying a reference to the figure it names.
    """
    found: list[tuple[int, int, str | None]] = []
    for match in INLINE_IMAGE.finditer(markdown):
        found.append((match.start(), match.end(), match.group(1)))
    start = markdown.find(PLACEHOLDER)
    while start != -1:
        found.append((start, start + len(PLACEHOLDER), None))
        start = markdown.find(PLACEHOLDER, start + 1)
    return sorted(found)


SKIPPED_NOTE = "*[a picture here was not extracted]*"


class PictureOutcome(StrEnum):
    EXTRACTED = "extracted"
    PAGE_SIZED = "page_sized"
    TOO_SMALL = "too_small"
    OVER_CEILING = "over_ceiling"
    UNUSABLE = "unusable"


@dataclass(frozen=True)
class PictureDecision:
    outcome: PictureOutcome
    payload: bytes | None = None
    mimetype: str | None = None
    page_no: int | None = None


@dataclass(frozen=True)
class PendingImage:
    """One picture on its way to the outbox.

    Carries either the bytes (a document converted whole) or the scratch file a part left
    behind (a document converted in pieces). Never both, and never the bytes of a whole
    document's worth of pictures at once — this service has been killed for less.
    """

    filename: str
    ordinal: int
    page_no: int | None
    mimetype: str
    payload: bytes | None = None
    source: object = None  # Path, kept loose so this module imports nothing it need not


class PlaceholderMismatch(RuntimeError):
    """The Markdown's placeholders and the engine's pictures do not correspond.

    Raised rather than reconciled: if the counts differ, every reference after the
    discrepancy would name the wrong figure, and a document that quietly cites the wrong
    picture is worse than one that cites none.
    """


def decode_data_uri(uri: object) -> tuple[bytes, str] | None:
    """Bytes and mimetype from a `data:` URI, or None if it is not one we can use.

    Anything that is not a `data:` URI is refused rather than fetched. The engine has no
    route to the internet and neither has this service (feature 001 FR-021), so a picture
    hosted somewhere is a picture that does not exist as far as we are concerned.
    """
    if not isinstance(uri, str) or not uri.startswith("data:"):
        return None
    header, separator, encoded = uri.partition(",")
    if not separator or not encoded or ";base64" not in header:
        return None
    mimetype = header[len("data:") :].split(";", 1)[0].strip().lower()
    try:
        return base64.b64decode(encoded, validate=True), mimetype
    except (binascii.Error, ValueError):
        return None


def page_coverage(bbox: dict, page_size: dict) -> float | None:
    """How much of its page a picture's bounding box covers, or None if unanswerable."""
    try:
        width = abs(float(bbox["r"]) - float(bbox["l"]))
        height = abs(float(bbox["t"]) - float(bbox["b"]))
        page_area = float(page_size["width"]) * float(page_size["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if page_area <= 0:
        return None
    return (width * height) / page_area


def plan_extraction(
    document: dict,
    *,
    coverage: float,
    min_bytes: int,
    max_per_document: int,
    inline: list[str | None] | None = None,
) -> list[PictureDecision]:
    """One decision per picture, in document order — the order the placeholders are in.

    The list is always as long as `pictures[]`, including for pictures that will produce
    no file, because the placeholders have to be rewritten position by position.
    """
    pictures = document.get("pictures")
    if not isinstance(pictures, list):
        return []
    pages = document.get("pages") if isinstance(document.get("pages"), dict) else {}

    decisions: list[PictureDecision] = []
    # The ceiling bounds the *folder*, and the folder holds distinct pictures: a letterhead
    # repeated on every page is one file however many times it appears (FR-005).
    distinct: set[str] = set()
    for index, picture in enumerate(pictures):
        fallback = (inline or [None] * len(pictures))[index] if inline else None
        decision = _decide(
            picture, pages, coverage, min_bytes, max_per_document, len(distinct), fallback
        )
        decisions.append(decision)
        if decision.outcome is PictureOutcome.EXTRACTED and decision.payload is not None:
            distinct.add(hashlib.sha256(decision.payload).hexdigest())
    return decisions


def _decide(
    picture: object,
    pages: dict,
    coverage: float,
    min_bytes: int,
    max_per_document: int,
    extracted: int,
    inline: str | None = None,
) -> PictureDecision:
    if not isinstance(picture, dict):
        return PictureDecision(PictureOutcome.UNUSABLE)

    image = picture.get("image") if isinstance(picture.get("image"), dict) else {}
    # The structure first, the Markdown second. Which of the two carries the bytes depends
    # on the export mode, and the two are not independent — so take them from wherever
    # they are rather than insisting on one.
    decoded = decode_data_uri(image.get("uri")) or decode_data_uri(inline)
    if decoded is None:
        return PictureDecision(PictureOutcome.UNUSABLE)
    payload, mimetype = decoded
    if mimetype not in IMAGE_EXTENSIONS:
        # A file no ordinary viewer opens is a reference that does not resolve (FR-003).
        return PictureDecision(PictureOutcome.UNUSABLE)

    prov = picture.get("prov")
    if not isinstance(prov, list) or not prov or not isinstance(prov[0], dict):
        return PictureDecision(PictureOutcome.UNUSABLE)
    page_no = prov[0].get("page_no")
    page = pages.get(str(page_no)) or pages.get(page_no)
    if not isinstance(page, dict):
        return PictureDecision(PictureOutcome.UNUSABLE)

    fraction = page_coverage(prov[0].get("bbox") or {}, page.get("size") or {})
    if fraction is None:
        return PictureDecision(PictureOutcome.UNUSABLE)
    if fraction >= coverage:
        # The page, not a figure on it. Its text is already in the Markdown (FR-004).
        return PictureDecision(PictureOutcome.PAGE_SIZED, page_no=page_no)
    if len(payload) < min_bytes:
        return PictureDecision(PictureOutcome.TOO_SMALL, page_no=page_no)
    if extracted >= max_per_document:
        return PictureDecision(PictureOutcome.OVER_CEILING, page_no=page_no)
    return PictureDecision(PictureOutcome.EXTRACTED, payload, mimetype, page_no)


def strip_placeholders(markdown: str) -> str:
    """Remove every picture marker, leaving the text as though none had been there.

    For when there are no pictures to point at: extraction turned off, or an engine that
    returned none. A marker is only worth keeping when it stands in for something the
    reader can reach.
    """
    tokens = image_tokens(markdown)
    if not tokens:
        return markdown
    rebuilt: list[str] = []
    cursor = 0
    for start, end, _ in tokens:
        rebuilt.append(markdown[cursor:start])
        cursor = end
    rebuilt.append(markdown[cursor:])
    # The blank lines the marker sat between would otherwise pile up where it was.
    return re.sub(r"\n{3,}", "\n\n", "".join(rebuilt))


def rewrite_placeholders(
    markdown: str,
    decisions: list[PictureDecision],
    filenames: list[str | None],
) -> str:
    """Replace each placeholder according to its picture's fate (FR-006).

    Three outcomes, and the difference between them matters: an extracted picture becomes
    a reference; a page-sized image leaves *nothing*, because a marker on every page of a
    scan is noise; anything else leaves a note, because a picture the operator cannot see
    is something they should at least know was there.
    """
    if not decisions:
        return markdown

    tokens = image_tokens(markdown)
    if len(tokens) != len(decisions):
        raise PlaceholderMismatch(f"{len(tokens)} image markers for {len(decisions)} pictures")

    rebuilt: list[str] = []
    cursor = 0
    for (start, end, _), decision, filename in zip(tokens, decisions, filenames, strict=True):
        rebuilt.append(markdown[cursor:start])
        if decision.outcome is PictureOutcome.EXTRACTED and filename:
            rebuilt.append(f"![]({filename})")
        elif decision.outcome is not PictureOutcome.PAGE_SIZED:
            rebuilt.append(SKIPPED_NOTE)
        cursor = end
    rebuilt.append(markdown[cursor:])
    return "".join(rebuilt)
