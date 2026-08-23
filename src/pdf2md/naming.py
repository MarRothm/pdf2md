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


def archive_filename(original_name: str, content_hash: str) -> str:
    """The download name for every file of one document, bundled (FR-043).

    Deliberately the document's name, not the first section's: what arrives in the
    browser should be recognisable as the thing that was uploaded.
    """
    return output_filename(original_name, content_hash).removesuffix(".md") + ".zip"
