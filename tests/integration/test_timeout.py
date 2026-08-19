"""A job that outruns the watchdog is stopped, and the queue keeps moving (FR-028)."""

import pytest

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
