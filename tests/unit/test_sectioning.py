"""Section splitting (FR-033, research.md R13).

These files are what an operator imports and what AnythingLLM cites, so the properties
that matter are: every byte of the document survives, the names are stable across runs,
and the count stays sane for a document with unusual heading structure.
"""

import pytest

from pdf2md.sectioning import Section, section_filename, split_into_sections

pytestmark = pytest.mark.unit

HASH = "4f2a91b0c7d3" + "0" * 52
BIG = 10**6


def _titles(sections):
    return [section.title for section in sections]


def test_splits_at_the_top_heading_level():
    markdown = "# Alpha\n\nbody a\n\n# Beta\n\nbody b\n"
    assert _titles(split_into_sections(markdown, min_bytes=1, max_bytes=BIG)) == ["Alpha", "Beta"]


def test_splits_at_the_highest_level_present_not_always_hash():
    """Plenty of converted documents start at `##`; keying on `#` would return one file
    for them, which is exactly the document that needed splitting."""
    markdown = "## Alpha\n\nbody a\n\n## Beta\n\nbody b\n"
    assert _titles(split_into_sections(markdown, min_bytes=1, max_bytes=BIG)) == ["Alpha", "Beta"]


def test_deeper_headings_do_not_start_a_new_file():
    markdown = "# Alpha\n\n## Sub\n\nbody\n\n# Beta\n\nbody\n"
    sections = split_into_sections(markdown, min_bytes=1, max_bytes=BIG)
    assert _titles(sections) == ["Alpha", "Beta"]
    assert "## Sub" in sections[0].markdown


def test_a_heading_inside_a_code_fence_is_not_a_heading():
    markdown = "# Alpha\n\n```\n# not a heading\n```\n\n# Beta\n\nbody\n"
    assert _titles(split_into_sections(markdown, min_bytes=1, max_bytes=BIG)) == ["Alpha", "Beta"]


def test_text_before_the_first_heading_is_kept():
    markdown = "front matter\n\n# Alpha\n\nbody\n"
    sections = split_into_sections(markdown, min_bytes=1, max_bytes=BIG)
    assert "front matter" in sections[0].markdown


def test_no_content_is_lost_in_the_split():
    markdown = "intro\n\n# Alpha\n\nbody a\n\n# Beta\n\nbody b\n"
    sections = split_into_sections(markdown, min_bytes=1, max_bytes=BIG)
    assert "".join(section.markdown for section in sections) == markdown


def test_a_document_without_headings_stays_one_file():
    markdown = "just prose, no structure at all\n"
    sections = split_into_sections(markdown, min_bytes=1, max_bytes=BIG)
    assert len(sections) == 1
    assert sections[0].markdown == markdown


def test_small_sections_merge_rather_than_becoming_their_own_files():
    """A document of many short headings would otherwise become hundreds of files, each
    too small to answer anything."""
    markdown = "".join(f"# H{index}\n\ntiny\n\n" for index in range(20))
    sections = split_into_sections(markdown, min_bytes=4096, max_bytes=BIG)
    assert len(sections) == 1


def test_an_oversized_section_is_divided_at_the_next_level_down():
    body = "x" * 20_000
    markdown = f"# Alpha\n\n## One\n\n{body}\n\n## Two\n\n{body}\n"
    sections = split_into_sections(markdown, min_bytes=1, max_bytes=10_000)
    assert _titles(sections) == ["One", "Two"]
    # the parent heading rides with the first piece rather than becoming a stub file
    assert sections[0].markdown.startswith("# Alpha")
    assert "".join(section.markdown for section in sections) == markdown


def test_an_oversized_section_with_no_subheadings_is_left_whole():
    """A hard byte split would cut mid-sentence and name nothing in its citation."""
    markdown = "# Alpha\n\n" + "x" * 50_000
    sections = split_into_sections(markdown, min_bytes=1, max_bytes=1000)
    assert len(sections) == 1


def test_filenames_are_ordered_traceable_and_stable():
    section = Section(ordinal=7, title="Configuration Options", markdown="body")
    name = section_filename("Big Manual.pdf", HASH, section)
    assert name == "big-manual--4f2a91b0c7d3--007-configuration-options.md"
    assert section_filename("Big Manual.pdf", HASH, section) == name


def test_a_section_with_no_usable_title_still_gets_a_name():
    assert section_filename("m.pdf", HASH, Section(1, "", "body")).endswith("--001-section.md")
