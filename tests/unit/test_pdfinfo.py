"""Reading a PDF's structure, and telling the three failure modes apart (research.md R11)."""

import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

from pdf2md import pdfinfo
from pdf2md.pdfinfo import (
    EncryptedPdfError,
    UnreadablePdfError,
    extract_range,
    page_count,
    plan_parts,
)
from tests.conftest import damaged_pdf_bytes, pdf_bytes

pytestmark = pytest.mark.unit


def _write(tmp_path, data: bytes, name: str = "doc.pdf"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_page_count_reads_the_page_tree(tmp_path):
    assert page_count(_write(tmp_path, pdf_bytes(b"a", pages=7))) == 7


def test_an_encrypted_pdf_is_distinguishable_from_a_damaged_one(tmp_path):
    """The user is told to remove the password, not that their file is broken (FR-007)."""
    with pytest.raises(EncryptedPdfError):
        page_count(_write(tmp_path, pdf_bytes(b"a", encrypted=True)))


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("no page tree", damaged_pdf_bytes()),
        ("truncated mid-file", pdf_bytes(b"a", pages=5)[:120]),
        ("header and noise", b"%PDF-1.7\n" + b"\x00\xff" * 200),
    ],
)
def test_a_damaged_pdf_raises_the_unreadable_error(tmp_path, name, data):
    """pypdf reports these as three different exception types; callers see one."""
    with pytest.raises(UnreadablePdfError):
        page_count(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("pages", "limit", "expected"),
    [
        (1, 100, [(1, 1)]),
        (100, 100, [(1, 100)]),
        (101, 100, [(1, 100), (101, 101)]),
        (250, 100, [(1, 100), (101, 200), (201, 250)]),
    ],
)
def test_plan_parts_covers_the_document_exactly_once(pages, limit, expected):
    assert plan_parts(pages, limit) == expected


def test_a_document_within_the_limit_is_one_part(tmp_path):
    """So the ordinary document needs no special case anywhere downstream."""
    assert len(plan_parts(42, 100)) == 1


def test_plan_parts_leaves_no_page_uncovered():
    ranges = plan_parts(2413, 100)
    covered = [page for first, last in ranges for page in range(first, last + 1)]
    assert covered == list(range(1, 2414))
    assert len(ranges) == 25


def test_extract_range_writes_exactly_the_pages_asked_for(tmp_path):
    source = _write(tmp_path, pdf_bytes(b"a", pages=10))
    destination = tmp_path / "part.pdf"
    extract_range(source, destination, 3, 6)
    assert page_count(destination) == 4


def test_extract_range_refuses_a_range_outside_the_document(tmp_path):
    source = _write(tmp_path, pdf_bytes(b"a", pages=5))
    with pytest.raises(ValueError):
        extract_range(source, tmp_path / "part.pdf", 4, 9)


def _deeply_nested_pdf(depth: int) -> bytes:
    """A one-page PDF whose page tree is `depth` levels deep.

    What merging a hundred documents into one produces: each merge wraps the tree in
    another intermediate node. Entirely legal, and pypdf 6.16.2 refuses it past 100.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    root = writer._pages  # the /Pages node the page hangs from

    for _ in range(depth - 1):
        intermediate = DictionaryObject()
        intermediate.update(
            {
                NameObject("/Type"): NameObject("/Pages"),
                NameObject("/Count"): NumberObject(1),
                NameObject("/Kids"): ArrayObject([root.get_object()["/Kids"][0]]),
            }
        )
        reference = writer._add_object(intermediate)
        root.get_object()[NameObject("/Kids")] = ArrayObject([reference])

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_a_deeply_nested_page_tree_is_not_a_damaged_file(tmp_path):
    """The regression that told an operator to re-export a working document.

    pypdf 6.16.2 caps the page tree at 100 levels; a contract collection assembled from a
    hundred PDFs is 101. The file is legal, the converter reads it with a different
    backend entirely, and this check must not be the strictest thing in the stack.
    """
    path = tmp_path / "merged.pdf"
    path.write_bytes(_deeply_nested_pdf(depth=120))

    assert page_count(path) == 1


def test_the_depth_ceiling_is_raised_but_still_a_ceiling():
    """Removed entirely, a page tree that recurses without end would run until Python's
    own stack gave out."""
    from pypdf import _doc_common

    assert _doc_common.PAGE_TREE_MAX_DEPTH == pdfinfo.PAGE_TREE_MAX_DEPTH
    assert 100 < pdfinfo.PAGE_TREE_MAX_DEPTH < 900
