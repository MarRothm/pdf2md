"""Split a long document's Markdown into one file per section (FR-033, research.md R13).

This exists for citations, not for retrieval. AnythingLLM chunks whatever it is given and
ranks chunks, so file boundaries do not change what it finds — but they do change what it
names when it answers. A 2000-page manual as one file cites "the manual"; as section files
it cites the chapter, which is the difference between a citation you can follow and one you
cannot.

Only documents above a size threshold are split this way. An ordinary document stays one
file, exactly as it always was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pdf2md.naming import HASH_PREFIX_LENGTH, slugify

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
MAX_SECTION_SLUG_LENGTH = 40


@dataclass(frozen=True)
class Section:
    ordinal: int
    title: str
    markdown: str


def split_into_sections(
    markdown: str,
    *,
    min_bytes: int,
    max_bytes: int,
) -> list[Section]:
    """Divide `markdown` at its top heading level, bounded on both sides.

    The split level is the *highest level actually present*, not `#`. Plenty of converted
    documents start at `##`, and a rule that keyed on `#` would hand them back as a single
    file — defeating the feature on exactly the documents that needed it.
    """
    headings = _headings(markdown)
    if not headings:
        return [Section(1, "", markdown)]

    top_level = min(level for level, _, _ in headings)
    blocks = _blocks_at(markdown, headings, top_level)
    blocks = _merge_small(blocks, min_bytes)
    blocks = _divide_large(blocks, headings, max_bytes, top_level)
    return [Section(index, title, text) for index, (title, text) in enumerate(blocks, start=1)]


def section_filename(original_name: str, content_hash: str, section: Section) -> str:
    """`{slug}--{hash12}--{ordinal:03d}-{section-slug}.md`.

    Deterministic for the same bytes through the same engine, so re-converting a document
    overwrites its files in place rather than accumulating a second set (FR-014). The
    ordinal keeps the document in reading order in Finder and in a document list.
    """
    document = slugify(original_name)
    if section.title.strip():
        title = slugify(section.title)[:MAX_SECTION_SLUG_LENGTH].strip("-") or "section"
    else:
        title = "section"
    return f"{document}--{content_hash[:HASH_PREFIX_LENGTH]}--{section.ordinal:03d}-{title}.md"


def _headings(markdown: str) -> list[tuple[int, str, int]]:
    """(level, title, line index) for every heading outside a code fence."""
    found: list[tuple[int, str, int]] = []
    in_fence = False
    for index, line in enumerate(markdown.splitlines()):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            found.append((len(match.group(1)), match.group(2), index))
    return found


def _blocks_at(
    markdown: str, headings: list[tuple[int, str, int]], level: int
) -> list[tuple[str, str]]:
    """Cut the text at every heading of `level`, keeping any preamble before the first."""
    lines = markdown.splitlines(keepends=True)
    cuts = [(title, line) for heading_level, title, line in headings if heading_level == level]

    blocks: list[tuple[str, str]] = []
    if cuts and cuts[0][1] > 0:
        preamble = "".join(lines[: cuts[0][1]])
        if preamble.strip():
            blocks.append(("", preamble))

    for position, (title, start) in enumerate(cuts):
        end = cuts[position + 1][1] if position + 1 < len(cuts) else len(lines)
        blocks.append((title, "".join(lines[start:end])))
    return blocks


def _merge_small(blocks: list[tuple[str, str]], min_bytes: int) -> list[tuple[str, str]]:
    """Fold a section too small to stand alone into the one before it.

    Without this a document of many short headings becomes hundreds of files, each too
    small to answer anything on its own — worse for the operator than the single file it
    replaced.
    """
    merged: list[tuple[str, str]] = []
    for title, text in blocks:
        if merged and len(text.encode("utf-8")) < min_bytes:
            previous_title, previous_text = merged[-1]
            merged[-1] = (previous_title, previous_text + text)
        else:
            merged.append((title, text))
    return merged


def _divide_large(
    blocks: list[tuple[str, str]],
    headings: list[tuple[int, str, int]],
    max_bytes: int,
    level: int,
) -> list[tuple[str, str]]:
    """Divide an oversized section at the next heading level down.

    If it has no subheadings either, it is left whole: a hard byte split would cut
    mid-sentence and produce a file whose citation names nothing.
    """
    levels_below = sorted({found for found, _, _ in headings if found > level})
    divided: list[tuple[str, str]] = []
    for title, text in blocks:
        if len(text.encode("utf-8")) <= max_bytes or not levels_below:
            divided.append((title, text))
            continue
        inner = _headings(text)
        next_level = next(
            (found for found in levels_below if any(f == found for f, _, _ in inner)), None
        )
        if next_level is None:
            divided.append((title, text))
            continue
        pieces = _blocks_at(text, inner, next_level)
        # The first piece is whatever preceded the first subheading — usually just the
        # parent heading itself. On its own that is a stub file whose citation says
        # nothing, so it rides along with the section that follows it.
        if len(pieces) > 1 and not pieces[0][0]:
            preamble = pieces.pop(0)[1]
            pieces[0] = (pieces[0][0], preamble + pieces[0][1])
        divided.extend((piece_title or title, piece_text) for piece_title, piece_text in pieces)
    return divided
