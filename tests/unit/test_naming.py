"""Output naming (FR-014, research.md R8)."""

import hashlib

import pytest

from pdf2md.naming import output_filename, slugify

pytestmark = pytest.mark.unit

HASH_A = hashlib.sha256(b"document-a").hexdigest()
HASH_B = hashlib.sha256(b"document-b").hexdigest()


def test_identical_bytes_give_the_same_name_under_the_same_filename():
    assert output_filename("Report.pdf", HASH_A) == output_filename("Report.pdf", HASH_A)


def test_identical_bytes_under_different_filenames_share_the_hash_suffix():
    first = output_filename("Report.pdf", HASH_A)
    second = output_filename("Report (copy).pdf", HASH_A)
    assert first != second
    assert first.split("--")[1] == second.split("--")[1] == f"{HASH_A[:12]}.md"


def test_different_bytes_under_one_filename_give_different_names():
    assert output_filename("Report.pdf", HASH_A) != output_filename("Report.pdf", HASH_B)


def test_name_shape_is_slug_hash12_md():
    assert output_filename("Annual Report 2026.pdf", HASH_A) == (
        f"annual-report-2026--{HASH_A[:12]}.md"
    )


@pytest.mark.parametrize(
    ("hostile", "expected_slug"),
    [
        ("../../etc/passwd.pdf", "passwd"),
        ("/absolute/path/report.pdf", "report"),
        ("..\\..\\windows\\system.pdf", "system"),
        ("....", "document"),
        ("   .pdf", "document"),
        ("a b\tc\nd.pdf", "a-b-c-d"),
        ('quote"and*star?.pdf', "quote-and-star"),
        ("日本語.pdf", "document"),
        ("café-résumé.pdf", "cafe-resume"),
    ],
)
def test_hostile_filenames_are_sanitized(hostile, expected_slug):
    name = output_filename(hostile, HASH_A)
    assert name == f"{expected_slug}--{HASH_A[:12]}.md"
    assert "/" not in name and "\\" not in name and ".." not in name


def test_very_long_names_are_bounded():
    name = output_filename("x" * 500 + ".pdf", HASH_A)
    assert len(name.split("--")[0]) <= 80


def test_slug_never_empty():
    assert slugify("???") == "document"


def test_content_hash_must_be_a_sha256_digest():
    with pytest.raises(ValueError):
        output_filename("report.pdf", "not-a-hash")
