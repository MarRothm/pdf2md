"""What the service needs to know about a PDF before the engine ever sees it.

Reading the page tree at upload is what makes three things possible: splitting a long
document (FR-034), refusing an impossibly long one for its length rather than as damage
(FR-036), and telling someone their PDF is password-protected in a second instead of after
a round trip through the engine (FR-007).

Structure only — no text, no rendering. Extraction is the engine's job (research.md R11).
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import FileNotDecryptedError


class PdfStructureError(Exception):
    """The PDF could not be understood well enough to decide what to do with it."""


class EncryptedPdfError(PdfStructureError):
    """Password-protected. Distinguished so the user is told the actionable thing."""


class UnreadablePdfError(PdfStructureError):
    """Damaged, truncated, or not really a PDF."""


def page_count(source: Path) -> int:
    """Number of pages, or a `PdfStructureError` saying why not.

    The catch is deliberately broad. `pypdf` is tolerant by design and rebuilds what it
    can, so a malformed file surfaces as whatever exception the recovery path happens to
    hit — `AttributeError` from a catalog with no page tree, as readily as its own
    `PdfReadError`. Every one of them means the same thing to a caller: this file cannot
    be read, and the person who uploaded it needs to be told so.
    """
    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            raise EncryptedPdfError(source.name)
        count = len(reader.pages)
    except EncryptedPdfError:
        raise
    except FileNotDecryptedError as error:
        raise EncryptedPdfError(source.name) from error
    except Exception as error:
        raise UnreadablePdfError(f"{source.name}: {error}") from error
    if count < 1:
        # Recovered enough to parse, not enough to convert.
        raise UnreadablePdfError(f"{source.name}: no readable pages")
    return count


def plan_parts(pages: int, part_max_pages: int) -> list[tuple[int, int]]:
    """Page ranges covering the document, 1-based and inclusive on both ends.

    A document at or under the limit yields a single range, so callers need no special
    case for the ordinary document — it is simply a document with one part.
    """
    if pages < 1:
        raise ValueError("a document with no pages cannot be split")
    if part_max_pages < 1:
        raise ValueError("part_max_pages must be at least 1")
    return [
        (first, min(first + part_max_pages - 1, pages))
        for first in range(1, pages + 1, part_max_pages)
    ]


def extract_range(source: Path, destination: Path, first_page: int, last_page: int) -> None:
    """Write pages `first_page` to `last_page` of `source` as a PDF at `destination`.

    Both bounds are 1-based and inclusive, matching `plan_parts` and the page numbers a
    person reads off the document — the failure message for a missing part names these
    numbers, so they have to mean what a reader expects (FR-035).
    """
    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            raise EncryptedPdfError(source.name)
        total = len(reader.pages)
        if not 1 <= first_page <= last_page <= total:
            raise ValueError(f"pages {first_page}-{last_page} are outside a {total}-page document")
        writer = PdfWriter()
        for index in range(first_page - 1, last_page):
            writer.add_page(reader.pages[index])
        with destination.open("wb") as file:
            writer.write(file)
    except (EncryptedPdfError, ValueError):
        raise
    except FileNotDecryptedError as error:
        raise EncryptedPdfError(source.name) from error
    except Exception as error:
        raise UnreadablePdfError(f"{source.name}: {error}") from error
