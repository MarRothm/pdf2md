"""A part that fails is tried again before it becomes a hole in the document (FR-038).

The failure these cover is the one that was met in practice: a 2038-page document whose
every full-size part failed — in about twenty seconds each, so not for want of time —
leaving a document that reported *Converted* and contained almost nothing, while its
38-page remainder converted normally. Splitting rescues a document from the page limit;
without retrying, it hands the whole document to whatever the next ceiling turns out to be.
"""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration

TIMED_OUT = TaskBehavior(
    task_status_on_finish="failure",
    result_status="failure",
    errors=["Conversion timed out after 2400 seconds"],
)


async def _detail(client, response):
    return (await client.get(f"/api/jobs/{response.json()['accepted'][0]['job_id']}")).json()


def _sized(settings, *, part_max_pages=20, retry_splits=2, min_pages=5):
    settings.part_max_pages = part_max_pages
    settings.part_retry_splits = retry_splits
    settings.part_min_pages = min_pages


async def test_a_part_that_runs_out_of_time_is_halved_and_converted(
    convert, client, storage, stub_engine, settings
):
    """The whole point: pages that do not fit in the time ceiling still reach the file."""
    _sized(settings)
    stub_engine.default_behavior = TaskBehavior(markdown="# Chapter\n\n" + "body " * 200)
    stub_engine.set_behavior("scan.pdf (pages 1-20)", TIMED_OUT)

    detail = await _detail(client, await convert(("scan.pdf", pdf_bytes(b"halve", pages=40))))

    assert detail["status"] == "succeeded"
    assert detail["missing_page_ranges"] is None
    # the original two parts, less the one that was halved, plus its halves
    assert detail["part_count"] == 3
    text = next(iter(storage.outbox_path.glob("*.md"))).read_text()
    assert "are missing from this document" not in text


async def test_the_halves_are_joined_in_reading_order(
    convert, client, storage, stub_engine, settings
):
    """The replacements take ordinals at the end of the table, so position has to come
    from the page numbers or a rescued part lands at the back of the document."""
    _sized(settings)
    for first, last in ((1, 10), (11, 20), (21, 40)):
        stub_engine.set_behavior(
            f"scan.pdf (pages {first}-{last})",
            TaskBehavior(markdown=f"# Pages {first}\n\n" + "body " * 200),
        )
    stub_engine.set_behavior("scan.pdf (pages 1-20)", TIMED_OUT)

    await convert(("scan.pdf", pdf_bytes(b"order", pages=40)))

    text = next(iter(storage.outbox_path.glob("*.md"))).read_text()
    assert [line for line in text.splitlines() if line.startswith("# Pages")] == [
        "# Pages 1",
        "# Pages 11",
        "# Pages 21",
    ]


async def test_a_range_that_never_converts_is_given_up_on_and_says_why(
    convert, client, stub_engine, settings
):
    """Halving is bounded: each attempt costs another timeout's worth of engine time."""
    _sized(settings)
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    for pages in ("1-20", "1-10", "1-5", "6-10"):
        stub_engine.set_behavior(f"bad.pdf (pages {pages})", TIMED_OUT)

    detail = await _detail(client, await convert(("bad.pdf", pdf_bytes(b"bad", pages=40))))

    assert detail["status"] == "succeeded_incomplete"
    # halved twice, down to the floor, and only then accepted as a gap
    assert detail["missing_page_ranges"] == [[1, 5], [6, 10]]
    assert [(part["first_page"], part["last_page"]) for part in detail["missing_parts"]] == [
        (1, 5),
        (6, 10),
    ]
    # the engine's own reason, not "some pages are missing"
    assert all("too long" in part["failure_reason"] for part in detail["missing_parts"])


async def test_a_part_is_not_halved_below_the_floor(convert, client, stub_engine, settings):
    """Below it the pages are the problem, not their number — halving only burns time."""
    _sized(settings, part_max_pages=10, min_pages=10)
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    stub_engine.set_behavior("floor.pdf (pages 1-10)", TIMED_OUT)

    detail = await _detail(client, await convert(("floor.pdf", pdf_bytes(b"floor", pages=30))))

    assert detail["missing_page_ranges"] == [[1, 10]]
    assert detail["part_count"] == 3


async def test_a_forgotten_part_is_converted_again_rather_than_left_missing(
    upload, client, dispatcher, stub_engine, settings
):
    """An engine restart loses its task ids. A whole document is resubmitted when that
    happens; a part used to become a permanent gap instead."""
    settings.part_max_pages = 10
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)

    body = (await upload(("restart.pdf", pdf_bytes(b"forgot", pages=25)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()
    stub_engine.reset_tasks()

    await dispatcher.drain()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "succeeded"
    assert detail["missing_page_ranges"] is None


async def test_a_part_gives_up_once_its_attempts_are_spent(
    upload, client, dispatcher, stub_engine, settings
):
    """Retrying forever would keep a document in flight against an engine that has lost
    every result, so the attempts are counted and the gap is eventually reported."""
    settings.part_max_pages = 10
    settings.part_max_attempts = 2
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    stub_engine.set_behavior("lost.pdf (pages 1-10)", TaskBehavior(result_http_error=404))

    body = (await upload(("lost.pdf", pdf_bytes(b"lost", pages=25)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.drain()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "succeeded_incomplete"
    assert detail["missing_page_ranges"] == [[1, 10]]
    assert detail["missing_parts"][0]["attempts"] == settings.part_max_attempts


async def test_a_single_part_document_still_reports_no_missing_parts(convert, client):
    detail = await _detail(client, await convert(("small.pdf", pdf_bytes(b"one", pages=2))))
    assert detail["missing_parts"] == []


async def test_spent_attempts_fall_through_to_a_smaller_range(
    upload, client, dispatcher, stub_engine, settings
):
    """An engine that dies on a range takes its task table with it, so the symptom is a
    lost task and the cure is a smaller part — not a third identical one."""
    _sized(settings, part_max_pages=20, min_pages=5)
    settings.part_max_attempts = 1
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    stub_engine.set_behavior("oom.pdf (pages 1-20)", TaskBehavior(result_http_error=404))

    body = (await upload(("oom.pdf", pdf_bytes(b"oom", pages=40)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.drain()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "succeeded"
    assert detail["missing_page_ranges"] is None
    assert detail["part_count"] == 3
