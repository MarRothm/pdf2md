"""The layout invariants of `contracts/page-layout.md`, asserted against the assets.

The repository has no browser automation and no JavaScript runner, and adding one would
put a browser download into an image and a CI pipeline whose defining property is that
they reach nothing on the internet (research.md R12). These assertions catch the
regressions that actually happen here — someone reintroducing `nowrap`, or adding a
column without a width — and quickstart.md covers what only a browser can answer.
"""

import re

import pytest

from pdf2md.main import STATIC_DIR

pytestmark = pytest.mark.unit

COMMENT_LINE = re.compile(r"^\s*(//|\*|/\*|<!--)")


def _text(name: str) -> str:
    return (STATIC_DIR / name).read_text()


def _code(name: str) -> str:
    """The asset with its comment lines removed, so prose cannot satisfy an assertion."""
    return "\n".join(line for line in _text(name).splitlines() if not COMMENT_LINE.match(line))


# --- L1, L2: the table cannot be widened by its contents --------------------


def test_the_jobs_table_uses_fixed_layout():
    css = _code("styles.css")
    block = css[css.index("table.jobs") : css.index("table.jobs") + 400]
    assert "table-layout: fixed" in block


def test_every_column_has_a_width():
    """Fixed layout takes its widths from the header row; a column without one is a bug."""
    html = _text("index.html")
    headers = re.findall(r"<th scope=\"col\"[^>]*>([^<]+)</th>", html)
    assert headers, "the documents table has no column headers"

    css = _code("styles.css")
    widths = re.findall(r"table\.jobs (?:th|td):nth-child\(\d+\)[^}]*width:\s*[\d.]+%", css)
    assert len(widths) == len(headers), (
        f"{len(headers)} columns but {len(widths)} width rules — "
        "every column needs one under fixed layout"
    )


# --- L3: nothing refuses to wrap -------------------------------------------


def test_no_cell_refuses_to_wrap():
    """`white-space: nowrap` on a cell is what let one long status set the table width."""
    offenders = [line.strip() for line in _code("styles.css").splitlines() if "nowrap" in line]
    assert offenders == []


def test_long_unbroken_text_breaks_instead_of_stretching():
    assert "overflow-wrap: anywhere" in _code("styles.css")


# --- FR-002: the preview is clamped, and says so ---------------------------


def test_the_detail_cell_is_clamped_to_a_fixed_number_of_lines():
    css = _code("styles.css")
    assert "-webkit-line-clamp" in css
    assert re.search(r"^\s*line-clamp:", css, re.M), "emit the unprefixed property too"


def test_the_page_detects_a_clamped_cell_and_offers_the_full_text():
    """CSS can hide the overflow but cannot report it; the page has to measure."""
    js = _code("app.js")
    assert "scrollHeight" in js and "clientHeight" in js


# --- L4: status is not colour alone ----------------------------------------


def test_status_is_carried_by_text_not_only_colour():
    js = _code("app.js")
    assert "job.display_status" in js


# --- L5: the row's actions are always present ------------------------------


def test_the_actions_column_exists():
    assert "Actions" in _text("index.html")


def test_the_download_link_is_labelled_rather_than_named_after_the_file():
    """A section filename is long enough to have been a width problem of its own."""
    js = _code("app.js")
    assert '"Download"' in js


# --- FR-026: no bulk affordance --------------------------------------------


def test_the_page_offers_no_bulk_selection():
    html = _text("index.html")
    assert 'type="checkbox"' not in html
    assert "select-all" not in html


# --- D1-D3: the detail view is a real modal dialog -------------------------


def test_the_page_has_a_dialog():
    assert "<dialog" in _text("index.html")


def test_the_dialog_is_opened_as_a_modal():
    """showModal() is what gives Escape, the focus trap, and focus return to the opener."""
    assert "showModal()" in _code("app.js")


def test_the_dialog_is_labelled_by_its_heading():
    html = _text("index.html")
    assert 'aria-labelledby="modal-title"' in html
    assert 'id="modal-title"' in html


def test_the_detail_view_shows_the_message_unclamped():
    """The clamp belongs to the row; the dialog is where the hidden text lives."""
    js = _code("app.js")
    body = js[js.index("function renderDetailDialog") :]
    body = body[: body.index("\nfunction ")]
    assert "clamp" not in body


def test_the_detail_view_lists_the_documents_files_not_only_this_jobs():
    js = _code("app.js")
    assert "document_outputs" in js


def test_a_delete_is_never_issued_without_a_confirmation():
    """X1: the page has exactly one DELETE, inside the function the confirm button calls."""
    js = _code("app.js")
    assert js.count('method: "DELETE"') == 1

    before = js[: js.index('method: "DELETE"')]
    enclosing = re.findall(r"function (\w+)", before)[-1]
    assert enclosing == "performDelete"


def test_the_entry_count_comes_from_a_filtered_query_not_the_loaded_rows():
    """X5: the list is capped by `limit`; an older sibling would go uncounted."""
    assert "content_hash=${encodeURIComponent" in _code("app.js")
