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


async def test_without_a_page_count_a_flat_floor_applies(convert, client, stub_engine, settings):
    stub_engine.set_behavior("short.pdf", TaskBehavior(markdown="x" * 199, page_count=None))
    stub_engine.set_behavior("long.pdf", TaskBehavior(markdown="y" * 201, page_count=None))
    assert settings.suspect_min_chars_floor == 200

    short = await _job(client, await convert(("short.pdf", pdf_bytes(b"short"))))
    long = await _job(client, await convert(("long.pdf", pdf_bytes(b"long"))))
    assert short["status"] == "succeeded_suspect"
    assert long["status"] == "succeeded"


async def test_the_suspect_output_is_written_like_any_other(convert, client, storage, stub_engine):
    stub_engine.set_behavior("blank.pdf", TaskBehavior(markdown="", page_count=2))
    detail = await _job(client, await convert(("blank.pdf", pdf_bytes(b"b"))))
    assert storage.outbox_file(detail["output_filename"]).read_text() == ""
    assert detail["output_bytes"] == 0
