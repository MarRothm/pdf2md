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
