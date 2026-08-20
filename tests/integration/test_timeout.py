"""A job that outruns the watchdog is stopped, and the queue keeps moving (FR-028)."""

import pytest

from pdf2md.clock import iso_ago
from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def test_a_job_past_the_timeout_is_reported_timed_out(
    upload, dispatcher, client, settings, storage, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(never_finishes=True)
    body = (await upload(("endless.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]

    for _ in range(3):  # submit, then the engine's pending and started polls
        await dispatcher.run_once()
    assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "running"

    settings.job_timeout_seconds = 0  # the watchdog fires on the next pass
    await dispatcher.run_once()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "timed_out"
    assert detail["display_status"] == "Timed out"
    assert detail["failure_reason"]
    assert detail["download_url"] is None
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_the_queue_keeps_moving_after_a_timeout(
    upload, dispatcher, client, settings, storage, stub_engine
):
    stub_engine.set_behavior("endless.pdf", TaskBehavior(never_finishes=True))
    await upload(("endless.pdf", pdf_bytes(b"a")))
    await dispatcher.run_once()

    settings.job_timeout_seconds = 0
    await dispatcher.run_once()
    settings.job_timeout_seconds = 2700

    await upload(("normal.pdf", pdf_bytes(b"b")))
    await dispatcher.drain()

    jobs = {job["filename"]: job for job in (await client.get("/api/jobs")).json()["jobs"]}
    assert jobs["endless.pdf"]["status"] == "timed_out"
    assert jobs["normal.pdf"]["status"] == "succeeded"
    assert len(list(storage.outbox_path.glob("*.md"))) == 1


async def test_a_timed_out_job_frees_its_slot(upload, dispatcher, db, settings, stub_engine):
    stub_engine.default_behavior = TaskBehavior(never_finishes=True)
    documents = [(f"doc-{index}.pdf", pdf_bytes(f"b{index}".encode())) for index in range(5)]
    await upload(*documents)
    await dispatcher.run_once()
    assert db.count_active() == settings.max_in_flight
    waiting = db.backlog().queued
    assert waiting > 0

    settings.job_timeout_seconds = 0
    await dispatcher.run_once()

    timed_out = db.list_jobs(statuses=["timed_out"], limit=50)
    assert len(timed_out) == settings.max_in_flight
    # The same pass that stopped them started the documents waiting behind them.
    assert db.count_active() > 0
    assert db.backlog().queued < waiting


async def test_a_document_waiting_in_the_queue_is_not_timed_out(
    upload, dispatcher, db, settings, stub_engine
):
    """A job has not overrun anything while it is still waiting its turn."""
    stub_engine.default_behavior = TaskBehavior(never_finishes=True)
    documents = [(f"doc-{index}.pdf", pdf_bytes(f"b{index}".encode())) for index in range(6)]
    await upload(*documents)
    await dispatcher.run_once()

    settings.job_timeout_seconds = 0
    dispatcher.expire_timeouts()  # no submission pass, so nothing new starts
    assert len(db.list_jobs(statuses=["timed_out"], limit=50)) == settings.max_in_flight
    assert db.backlog().queued == 6 - settings.max_in_flight


async def test_a_split_document_is_not_timed_out_for_taking_longer_than_one_part(
    upload, client, dispatcher, stub_engine, settings, db
):
    """The watchdog is one conversion's allowance, and a split document is many.

    Measured from the job's creation it would terminate every document that splitting
    exists to rescue — after burning the engine time (research.md R12).
    """
    settings.part_max_pages = 10
    settings.job_timeout_seconds = 60
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)

    body = (await upload(("long.pdf", pdf_bytes(b"slow", pages=40)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()

    # The job was created long enough ago to trip a per-document watchdog.
    with db.connection() as conn:
        conn.execute(
            "UPDATE conversion_job SET created_at = ? WHERE id = ?", (iso_ago(hours=1), job_id)
        )

    await dispatcher.drain()
    assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "succeeded"


async def test_a_part_that_overruns_is_timed_out_on_its_own_clock(
    upload, client, dispatcher, stub_engine, settings, db
):
    settings.part_max_pages = 10
    settings.part_min_pages = 10  # at the floor, so the range is reported rather than halved
    settings.job_timeout_seconds = 60
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    stub_engine.set_behavior("stuck.pdf (pages 1-10)", TaskBehavior(never_finishes=True))

    body = (await upload(("stuck.pdf", pdf_bytes(b"stuck", pages=25)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()
    with db.connection() as conn:
        conn.execute(
            "UPDATE conversion_part SET started_at = ? WHERE ordinal = 1", (iso_ago(hours=1),)
        )

    await dispatcher.drain()
    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "succeeded_incomplete"
    assert detail["missing_page_ranges"] == [[1, 10]]
