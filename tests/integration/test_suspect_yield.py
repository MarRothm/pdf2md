"""Implausibly small conversions are reported distinctly, not as failures (FR-029)."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def _job(client, response):
    job_id = response.json()["accepted"][0]["job_id"]
    return (await client.get(f"/api/jobs/{job_id}")).json()


async def test_a_blank_scan_reports_suspect_and_is_still_downloadable(
    convert, client, storage, stub_engine
):
    stub_engine.set_behavior("blank-scan.pdf", TaskBehavior(markdown="", page_count=3))
    detail = await _job(client, await convert(("blank-scan.pdf", pdf_bytes(b"blank"))))

    assert detail["status"] == "succeeded_suspect"
    assert detail["display_status"] == "Converted — check output"
    assert detail["failure_reason"] is None
    assert detail["download_url"]
    assert storage.outbox_file(detail["output_filename"]).is_file()
    assert (await client.get(detail["download_url"])).status_code == 200


async def test_a_near_empty_result_trips_the_per_page_threshold(convert, client, stub_engine):
    stub_engine.set_behavior(
        "thin.pdf", TaskBehavior(markdown="# Report\n\nsee attached", page_count=20)
    )
    detail = await _job(client, await convert(("thin.pdf", pdf_bytes(b"thin"))))
    assert detail["status"] == "succeeded_suspect"


async def test_a_normal_document_does_not_trip_the_threshold(convert, client, stub_engine):
    stub_engine.set_behavior("normal.pdf", TaskBehavior(markdown="word " * 400, page_count=20))
    detail = await _job(client, await convert(("normal.pdf", pdf_bytes(b"normal"))))
    assert detail["status"] == "succeeded"
    assert detail["display_status"] == "Converted"


async def test_the_page_count_comes_from_the_pdf_even_when_the_engine_omits_it(
    convert, client, stub_engine, settings
):
    """Since the page count is read at upload (FR-036), the engine no longer has to supply
    it — so the per-page threshold applies where the flat floor used to.

    199 characters on the fixture's single page clears 50 characters per page comfortably,
    where the old flat floor of 200 would have called it suspect. This is the more accurate
    answer: the threshold is now measured against the document's real length.
    """
    stub_engine.set_behavior("short.pdf", TaskBehavior(markdown="x" * 199, page_count=None))
    detail = await _job(client, await convert(("short.pdf", pdf_bytes(b"short"))))
    assert detail["page_count"] == 1
    assert detail["status"] == "succeeded"


async def test_the_flat_floor_still_covers_a_document_with_no_page_count(dispatcher):
    """The fallback survives for rows that predate upload-time counting (FR-029)."""
    assert dispatcher.is_suspect_yield("x" * 199, None) is True
    assert dispatcher.is_suspect_yield("y" * 201, None) is False


async def test_the_suspect_output_is_written_like_any_other(convert, client, storage, stub_engine):
    stub_engine.set_behavior("blank.pdf", TaskBehavior(markdown="", page_count=2))
    detail = await _job(client, await convert(("blank.pdf", pdf_bytes(b"b"))))
    assert storage.outbox_file(detail["output_filename"]).read_text() == ""
    assert detail["output_bytes"] == 0
