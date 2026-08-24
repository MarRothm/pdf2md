"""Output naming: `{slug}--{content_hash[:12]}.md` (research.md R8).

The name is a pure function of the original filename and the PDF bytes, so
re-converting the same document overwrites in place and never produces a second
file for AnythingLLM to ingest (FR-014).
"""

from __future__ import annotations

import re
import unicodedata

HASH_PREFIX_LENGTH = 12
MAX_SLUG_LENGTH = 80
_UNSAFE = re.compile(r"[^a-z0-9]+")


def slugify(original_name: str) -> str:
    """Reduce an arbitrary uploaded filename to a safe, recognizable slug."""
    stem = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[: -len(".pdf")]

    normalized = unicodedata.normalize("NFKD", stem)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _UNSAFE.sub("-", ascii_only).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].strip("-")
    return slug or "document"


def output_filename(original_name: str, content_hash: str) -> str:
    """The outbox filename for a document, identical for identical bytes."""
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("content_hash must be a lowercase hex SHA-256 digest")
    return f"{slugify(original_name)}--{content_hash[:HASH_PREFIX_LENGTH]}.md"


IMAGE_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
}


def image_filename(original_name: str, content_hash: str, ordinal: int, mimetype: str) -> str:
    """`{slug}--{hash12}--img{NNN}.{ext}` for one extracted picture (FR-007).

    The document's own prefix, so its files sort together in the folder the operator
    opens, and an ordinal in document order so a reference is stable across conversions.

    An unknown mimetype raises rather than guessing an extension: a file the operator's
    viewer cannot open is a reference that does not resolve, which FR-003 calls a defect.
    """
    extension = IMAGE_EXTENSIONS.get(mimetype.split(";")[0].strip().lower())
    if extension is None:
        raise ValueError(f"not a storable image type: {mimetype}")
    if ordinal < 1:
        raise ValueError("image ordinals are 1-based")
    stem = output_filename(original_name, content_hash).removesuffix(".md")
    return f"{stem}--img{ordinal:03d}.{extension}"


def archive_filename(original_name: str, content_hash: str) -> str:
    """The download name for every file of one document, bundled (FR-043).

    Deliberately the document's name, not the first section's: what arrives in the
    browser should be recognisable as the thing that was uploaded.
    """
    return output_filename(original_name, content_hash).removesuffix(".md") + ".zip"
