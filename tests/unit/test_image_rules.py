"""Deciding what is a figure, and what the Markdown says where one was (feature 003).

Every rule here is pure: bytes in, decisions out, no engine and no filesystem. That is
deliberate — this is where the feature is actually decided, and it is the one part that can
be got right before anything touches a disk.
"""

import base64

import pytest

from pdf2md.images import (
    PLACEHOLDER,
    PictureOutcome,
    PlaceholderMismatch,
    decode_data_uri,
    page_coverage,
    plan_extraction,
    rewrite_placeholders,
    strip_placeholders,
)

pytestmark = pytest.mark.unit

A4 = {"width": 595.0, "height": 842.0}
BIG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 800  # comfortably over the 4096 floor


def _uri(payload: bytes = BIG, mimetype: str = "image/png") -> str:
    return f"data:{mimetype};base64," + base64.b64encode(payload).decode()


def _picture(*, bbox=(72.0, 700.0, 300.0, 500.0), payload=BIG, mimetype="image/png", page=1):
    left, top, right, bottom = bbox
    return {
        "image": {"mimetype": mimetype, "uri": _uri(payload, mimetype)},
        "prov": [{"page_no": page, "bbox": {"l": left, "t": top, "r": right, "b": bottom}}],
    }


def _document(*pictures, page_size=A4):
    return {
        "pictures": list(pictures),
        "pages": {"1": {"size": page_size, "page_no": 1}},
    }


def _plan(document, *, coverage=0.8, min_bytes=4096, max_per_document=500):
    return plan_extraction(
        document,
        coverage=coverage,
        min_bytes=min_bytes,
        max_per_document=max_per_document,
    )


# --- decoding -------------------------------------------------------------


def test_a_data_uri_decodes_to_bytes_and_a_type():
    assert decode_data_uri(_uri()) == (BIG, "image/png")


def test_a_uri_that_is_not_a_data_uri_is_refused():
    """The engine has no route out and neither has this service: a picture hosted
    somewhere is a picture we do not fetch (engine contract, rule 5)."""
    assert decode_data_uri("https://example.invalid/figure.png") is None
    assert decode_data_uri("") is None
    assert decode_data_uri(None) is None


def test_undecodable_base64_is_refused_rather_than_written():
    assert decode_data_uri("data:image/png;base64,not-base64!!") is None


# --- the rule itself ------------------------------------------------------


def test_coverage_is_the_box_against_the_page():
    assert page_coverage({"l": 0, "t": 842, "r": 595, "b": 0}, A4) == pytest.approx(1.0)
    assert page_coverage({"l": 0, "t": 421, "r": 595, "b": 0}, A4) == pytest.approx(0.5)


def test_a_picture_just_under_the_threshold_is_a_figure():
    """0.79 of the page: a figure with margins, a header, and a caption around it."""
    height = 842.0 * 0.79
    (decision,) = _plan(_document(_picture(bbox=(0.0, height, 595.0, 0.0))))
    assert decision.outcome is PictureOutcome.EXTRACTED


def test_a_picture_just_over_the_threshold_is_the_page():
    """0.81: a scanned page. Its text is already in the Markdown (FR-004)."""
    height = 842.0 * 0.81
    (decision,) = _plan(_document(_picture(bbox=(0.0, height, 595.0, 0.0))))
    assert decision.outcome is PictureOutcome.PAGE_SIZED


def test_a_picture_below_the_floor_is_a_rule_or_a_bullet():
    (decision,) = _plan(_document(_picture(payload=b"tiny")))
    assert decision.outcome is PictureOutcome.TOO_SMALL


def test_the_ceiling_stops_a_document_filling_the_outbox():
    """Counted in distinct pictures, because that is what fills the folder."""
    three = [
        _picture(payload=BIG + b"a"),
        _picture(payload=BIG + b"b"),
        _picture(payload=BIG + b"c"),
    ]
    decisions = _plan(_document(*three), max_per_document=2)
    assert [d.outcome for d in decisions] == [
        PictureOutcome.EXTRACTED,
        PictureOutcome.EXTRACTED,
        PictureOutcome.OVER_CEILING,
    ]


def test_the_same_picture_repeated_costs_one_place_at_the_ceiling():
    """A letterhead on every page must not spend the whole allowance on itself."""
    letterhead = _picture()
    decisions = _plan(
        _document(letterhead, letterhead, letterhead, _picture(payload=BIG + b"figure")),
        max_per_document=2,
    )
    assert [d.outcome for d in decisions] == [PictureOutcome.EXTRACTED] * 4


def test_a_picture_without_provenance_is_not_guessed_at():
    """No page and no box means no way to tell a figure from a scan, and inventing one
    would extract two thousand page images from a scanned contract."""
    picture = _picture()
    del picture["prov"]
    (decision,) = _plan(_document(picture))
    assert decision.outcome is PictureOutcome.UNUSABLE


def test_a_picture_whose_page_is_unknown_is_not_guessed_at():
    (decision,) = _plan(_document(_picture(page=99)))
    assert decision.outcome is PictureOutcome.UNUSABLE


def test_a_type_that_cannot_be_stored_is_refused_early():
    """Better an unusable picture than a file the operator's viewer cannot open — that
    would be a reference that does not resolve, which FR-003 calls a defect."""
    (decision,) = _plan(_document(_picture(mimetype="image/x-nonesuch")))
    assert decision.outcome is PictureOutcome.UNUSABLE


# --- what the Markdown ends up saying (FR-006) ----------------------------


def test_an_extracted_picture_becomes_a_reference():
    markdown = f"Before\n\n{PLACEHOLDER}\n\nAfter"
    decisions = _plan(_document(_picture()))

    result = rewrite_placeholders(markdown, decisions, ["doc--abc123--img001.png"])

    assert "![](doc--abc123--img001.png)" in result
    assert PLACEHOLDER not in result


def test_a_page_sized_image_leaves_nothing_at_all():
    """A marker on every page of a two-thousand-page scan is noise in the file and in the
    knowledge base alike (FR-006)."""
    height = 842.0 * 0.9
    markdown = f"Page text\n\n{PLACEHOLDER}\n\nMore text"
    decisions = _plan(_document(_picture(bbox=(0.0, height, 595.0, 0.0))))

    result = rewrite_placeholders(markdown, decisions, [None])

    assert PLACEHOLDER not in result
    assert "picture" not in result.lower()
    assert "Page text" in result and "More text" in result


def test_a_skipped_picture_leaves_a_note():
    """Information the operator would otherwise lose silently (FR-006)."""
    markdown = f"Text\n\n{PLACEHOLDER}\n"
    decisions = _plan(_document(_picture(payload=b"tiny")))

    result = rewrite_placeholders(markdown, decisions, [None])

    assert PLACEHOLDER not in result
    assert "picture" in result.lower()


def test_a_count_mismatch_is_reported_not_reconciled():
    """A reference pointing at the wrong figure is worse than no reference at all."""
    markdown = f"{PLACEHOLDER}\n\n{PLACEHOLDER}"
    decisions = _plan(_document(_picture()))

    with pytest.raises(PlaceholderMismatch):
        rewrite_placeholders(markdown, decisions, ["doc--abc123--img001.png"])


def test_no_pictures_leaves_the_markdown_exactly_as_it_was():
    markdown = "# Plain document\n\nNothing but text.\n"
    assert rewrite_placeholders(markdown, [], []) == markdown


def test_placeholders_are_cleared_when_there_is_nothing_to_point_at():
    """Extraction off means no pictures and no files — not a comment where each stood."""
    markdown = f"# Contract\n\n{PLACEHOLDER}\n\nClause one.\n\n{PLACEHOLDER}\n"
    result = strip_placeholders(markdown)

    assert PLACEHOLDER not in result
    assert "# Contract" in result and "Clause one." in result


def test_stripping_leaves_a_document_without_pictures_alone():
    markdown = "# Contract\n\nClause one.\n"
    assert strip_placeholders(markdown) == markdown


def test_stripping_does_not_leave_a_hole_in_the_text():
    markdown = f"One.\n\n{PLACEHOLDER}\n\nTwo.\n"
    assert strip_placeholders(markdown) == "One.\n\nTwo.\n"


def test_bytes_are_taken_from_the_markdown_when_the_structure_has_none():
    """The two exports are made from one copy, so which of them carries the picture bytes
    depends on a mode this service does not fully control. Take them from either."""
    picture = _picture()
    picture["image"] = {"mimetype": "image/png"}  # geometry, but no data
    (decision,) = plan_extraction(
        _document(picture),
        coverage=0.8,
        min_bytes=4096,
        max_per_document=500,
        inline=[_uri()],
    )
    assert decision.outcome is PictureOutcome.EXTRACTED
    assert decision.payload == BIG


def test_an_inline_picture_is_a_marker_like_any_other():
    """`embedded` mode writes the picture into the Markdown instead of a comment."""
    markdown = f"Before\n\n![]({_uri()})\n\nAfter"
    decisions = plan_extraction(
        _document(_picture()), coverage=0.8, min_bytes=4096, max_per_document=500
    )

    result = rewrite_placeholders(markdown, decisions, ["doc--abc123--img001.png"])

    assert "![](doc--abc123--img001.png)" in result
    assert "data:image" not in result
