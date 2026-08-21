"""Nothing is left non-terminal after a restart (User Story 5 scenario 3)."""

import httpx
import pytest

from pdf2md.main import create_app
from pdf2md.models import IN_FLIGHT_STATUSES
from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def restarted(settings, stub_engine):
    app = create_app(settings=settings, engine_transport=httpx.ASGITransport(app=stub_engine.app))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, app


async def test_in_flight_jobs_are_resubmitted_with_a_second_attempt(
    upload, dispatcher, client, settings, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(polls_pending=1, polls_running=5)
    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()
    await dispatcher.run_once()
    assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] in {"submitted", "running"}

    # The engine forgets its tasks when it restarts, so ours must not be polled again.
    stub_engine.reset_tasks()
    stub_engine.default_behavior = TaskBehavior()

    async for restarted_client, app in restarted(settings, stub_engine):
        after_restart = (await restarted_client.get(f"/api/jobs/{job_id}")).json()
        assert after_restart["status"] == "queued"
        assert after_restart["attempt"] == 2

        await app.state.dispatcher.drain()
        finished = (await restarted_client.get(f"/api/jobs/{job_id}")).json()
        assert finished["status"] == "succeeded"
        assert finished["attempt"] == 2


async def test_a_job_whose_upload_is_gone_is_failed_with_a_restart_reason(
    upload, dispatcher, client, storage, settings, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(polls_pending=1, polls_running=5)
    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()
    content_hash = (await client.get(f"/api/jobs/{job_id}")).json()["content_hash"]
    storage.delete_inbox_file(content_hash)

    async for restarted_client, _ in restarted(settings, stub_engine):
        detail = (await restarted_client.get(f"/api/jobs/{job_id}")).json()
        assert detail["status"] == "failed"
        assert "restart" in detail["failure_reason"].lower()
        assert "upload it again" in detail["failure_reason"].lower()


async def test_nothing_remains_non_terminal_after_a_restart_mid_batch(
    upload, dispatcher, settings, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(polls_pending=1, polls_running=3)
    documents = [(f"doc-{index}.pdf", pdf_bytes(f"body {index}".encode())) for index in range(10)]
    await upload(*documents)
    await dispatcher.run_once()
    await dispatcher.run_once()

    stub_engine.reset_tasks()
    stub_engine.default_behavior = TaskBehavior()

    async for restarted_client, app in restarted(settings, stub_engine):
        await app.state.dispatcher.drain()
        jobs = (await restarted_client.get("/api/jobs?limit=100")).json()["jobs"]
        assert len(jobs) == 10
        assert not [job for job in jobs if job["status"] in {s.value for s in IN_FLIGHT_STATUSES}]
        assert {job["status"] for job in jobs} == {"succeeded"}


async def test_an_engine_that_forgot_a_task_leads_to_a_resubmission_not_a_stuck_job(
    upload, dispatcher, client, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(polls_pending=1, polls_running=5)
    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()

    stub_engine.reset_tasks()  # the engine restarted underneath us
    stub_engine.default_behavior = TaskBehavior()
    await dispatcher.run_once()

    # The pass that notices the forgotten task also resubmits it — never a stuck job.
    recovered = (await client.get(f"/api/jobs/{job_id}")).json()
    assert recovered["status"] in {"queued", "submitted"}
    assert recovered["attempt"] == 2

    await dispatcher.drain()
    assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "succeeded"


async def test_a_restart_mid_split_resubmits_only_the_unfinished_parts(
    upload, client, dispatcher, stub_engine, settings, db
):
    """A part that already converted keeps its Markdown; only the rest go back."""
    settings.part_max_pages = 10
    settings.parts_in_flight = 1
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)

    body = (await upload(("long.pdf", pdf_bytes(b"restart", pages=40)))).json()
    job_id = body["accepted"][0]["job_id"]
    for _ in range(4):  # get at least one part converted
        await dispatcher.run_once()
    converted = [part for part in db.parts_for_job(job_id) if part.markdown]
    assert converted, "the test needs at least one finished part to be meaningful"

    stub_engine.reset_tasks()
    dispatcher.recover_in_flight()

    kept = [part for part in db.parts_for_job(job_id) if part.markdown]
    assert len(kept) == len(converted)

    await dispatcher.drain()
    assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "succeeded"


async def test_a_split_document_whose_upload_vanished_names_the_missing_pages(
    upload, client, dispatcher, stub_engine, storage, settings, db
):
    settings.part_max_pages = 10
    settings.parts_in_flight = 1
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)

    body = (await upload(("gone.pdf", pdf_bytes(b"gone", pages=30)))).json()
    job_id = body["accepted"][0]["job_id"]
    for _ in range(4):
        await dispatcher.run_once()

    content_hash = db.get_job(job_id).content_hash
    storage.delete_inbox_file(content_hash)
    storage.delete_part_files(content_hash)
    await dispatcher.drain()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] in {"succeeded_incomplete", "failed"}
    if detail["status"] == "succeeded_incomplete":
        assert detail["missing_page_ranges"]


async def test_a_queued_job_whose_parts_are_with_the_engine_is_not_abandoned(
    upload, client, dispatcher, db, stub_engine, settings
):
    """Nobody polls a `queued` job, and a job with parts in flight submits nothing more.

    Left alone the two states deadlock: the document waits for ever, logs nothing, and the
    status strip reports a converter standing ready (FR-041).
    """
    settings.part_max_pages = 10
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)

    body = (await upload(("stuck.pdf", pdf_bytes(b"stuck", pages=25)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()  # submits the first parts and marks the job submitted

    # The state a crash between the two writes leaves behind: parts with the engine,
    # job back in the queue.
    with db.connection() as conn:
        conn.execute("UPDATE conversion_job SET status = 'queued' WHERE id = ?", (job_id,))

    await dispatcher.drain()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "succeeded"


async def test_a_queued_job_whose_parts_all_failed_is_finished(
    upload, client, dispatcher, db, stub_engine, settings
):
    """The join is only ever called from the polling path, which a queued job never reaches."""
    settings.part_max_pages = 10
    settings.part_min_pages = 10
    stub_engine.default_behavior = TaskBehavior(task_status_on_finish="failure")

    body = (await upload(("doomed.pdf", pdf_bytes(b"doomed", pages=25)))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.drain()

    with db.connection() as conn:
        conn.execute("UPDATE conversion_job SET status = 'queued' WHERE id = ?", (job_id,))

    await dispatcher.run_once()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] in {"failed", "succeeded_incomplete"}


async def test_a_document_that_keeps_stopping_the_service_is_given_up_on(
    upload, client, app, db, settings
):
    """A crash loop takes every other document with it, and cannot be escaped from the
    page, because the page is down too (FR-042)."""
    settings.job_max_attempts = 3
    body = (await upload(("heavy.pdf", pdf_bytes(b"heavy", pages=2)))).json()
    job_id = body["accepted"][0]["job_id"]

    dispatcher = app.state.dispatcher
    for _ in range(settings.job_max_attempts + 1):
        dispatcher.recover_in_flight()  # what a restart does

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "failed"
    assert "smaller pieces" in detail["failure_reason"]


async def test_an_ordinary_restart_still_resumes(upload, client, app, settings):
    settings.job_max_attempts = 8
    body = (await upload(("fine.pdf", pdf_bytes(b"fine", pages=2)))).json()
    job_id = body["accepted"][0]["job_id"]

    app.state.dispatcher.recover_in_flight()

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["status"] == "queued"
