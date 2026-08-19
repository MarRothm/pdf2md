"""Converting a document that is too long for the engine to take in one go (FR-034).

The stub engine returns each part's Markdown, so these exercise the real machinery:
extraction into page ranges, the in-flight cap, per-part polling, and the join.
"""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def _detail(client, response):
    return (await client.get(f"/api/jobs/{response.json()['accepted'][0]['job_id']}")).json()


async def test_a_long_document_is_split_converted_and_joined(
    convert, client, storage, stub_engine, settings
):
    settings.part_max_pages = 10
    # Enough text per part to clear the suspect-yield threshold for 35 pages, so this
    # test measures splitting rather than FR-029.
    stub_engine.default_behavior = TaskBehavior(markdown="# Chapter\n\n" + "body " * 200)

    detail = await _detail(client, await convert(("long.pdf", pdf_bytes(b"long", pages=35))))

    assert detail["status"] == "succeeded"
    assert detail["part_count"] == 4
    assert detail["parts_completed"] == 4
    assert detail["missing_page_ranges"] is None
    # one document in, one document out — the parts were bookkeeping
    written = list(storage.outbox_path.glob("*.md"))
    assert len(written) == 1
    assert written[0].read_text().count("# Chapter") == 4


async def test_a_document_within_the_limit_is_not_split(convert, client, settings):
    settings.part_max_pages = 10
    detail = await _detail(client, await convert(("short.pdf", pdf_bytes(b"short", pages=4))))

    assert detail["status"] == "succeeded"
    assert detail["part_count"] == 1


async def test_only_a_few_parts_are_in_flight_at_once(upload, dispatcher, stub_engine, settings):
    """A long document must not take the whole queue in front of everyone else's."""
    settings.part_max_pages = 10
    settings.parts_in_flight = 2
    stub_engine.default_behavior = TaskBehavior(polls_pending=5)

    await upload(("long.pdf", pdf_bytes(b"cap", pages=50)))
    await dispatcher.run_once()
    await dispatcher.run_once()

    assert len(stub_engine.tasks) <= settings.parts_in_flight


async def test_one_failing_part_does_not_discard_the_others(
    convert, client, storage, stub_engine, settings
):
    """Nineteen good parts are worth more than a clean failure (FR-035)."""
    settings.part_max_pages = 10
    stub_engine.default_behavior = TaskBehavior(markdown="good content")
    stub_engine.set_behavior(
        "long.pdf (pages 11-20)", TaskBehavior(task_status_on_finish="failure")
    )

    detail = await _detail(client, await convert(("long.pdf", pdf_bytes(b"gap", pages=30))))

    assert detail["status"] == "succeeded_incomplete"
    assert detail["missing_page_ranges"] == [[11, 20]]
    assert "missing" in detail["display_status"].lower()

    text = next(iter(storage.outbox_path.glob("*.md"))).read_text()
    assert "good content" in text
    # the gap is in the file, not only on the page: history is pruned, the file is not
    assert "Pages 11-20 are missing" in text


async def test_a_document_whose_every_part_fails_writes_nothing(
    convert, client, storage, stub_engine, settings
):
    settings.part_max_pages = 10
    stub_engine.default_behavior = TaskBehavior(task_status_on_finish="failure")

    detail = await _detail(client, await convert(("doomed.pdf", pdf_bytes(b"d", pages=25))))

    assert detail["status"] == "failed"
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_the_page_shows_which_part_is_converting(
    upload, client, dispatcher, stub_engine, settings
):
    """An hour with no visible movement is indistinguishable from a stall (FR-037)."""
    settings.part_max_pages = 10
    settings.parts_in_flight = 1
    stub_engine.default_behavior = TaskBehavior(polls_pending=0, polls_running=1)

    body = (await upload(("long.pdf", pdf_bytes(b"progress", pages=40)))).json()
    job_id = body["accepted"][0]["job_id"]
    for _ in range(4):
        await dispatcher.run_once()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["part_count"] == 4
    assert detail["display_status"].startswith("Converting — part ")


async def test_part_files_do_not_outlive_the_conversion(convert, client, storage, settings):
    settings.part_max_pages = 10
    await convert(("long.pdf", pdf_bytes(b"cleanup", pages=25)))
    assert list(storage.inbox_path.glob("*--part*.pdf")) == []
