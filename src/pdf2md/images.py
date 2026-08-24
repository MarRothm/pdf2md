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
from dataclasses import dataclass
from enum import StrEnum

from pdf2md.naming import IMAGE_EXTENSIONS

PLACEHOLDER = "<!-- image -->"
"""What `image_export_mode=placeholder` leaves behind (contracts/docling-serve-images.md)."""

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
    extracted = 0
    for picture in pictures:
        decisions.append(_decide(picture, pages, coverage, min_bytes, max_per_document, extracted))
        if decisions[-1].outcome is PictureOutcome.EXTRACTED:
            extracted += 1
    return decisions


def _decide(
    picture: object,
    pages: dict,
    coverage: float,
    min_bytes: int,
    max_per_document: int,
    extracted: int,
) -> PictureDecision:
    if not isinstance(picture, dict):
        return PictureDecision(PictureOutcome.UNUSABLE)

    image = picture.get("image") if isinstance(picture.get("image"), dict) else {}
    decoded = decode_data_uri(image.get("uri"))
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

    parts = markdown.split(PLACEHOLDER)
    if len(parts) - 1 != len(decisions):
        raise PlaceholderMismatch(f"{len(parts) - 1} placeholders for {len(decisions)} pictures")

    rebuilt = [parts[0]]
    for decision, filename, tail in zip(decisions, filenames, parts[1:], strict=True):
        if decision.outcome is PictureOutcome.EXTRACTED and filename:
            rebuilt.append(f"![]({filename})")
        elif decision.outcome is not PictureOutcome.PAGE_SIZED:
            rebuilt.append(SKIPPED_NOTE)
        rebuilt.append(tail)
    return "".join(rebuilt)
