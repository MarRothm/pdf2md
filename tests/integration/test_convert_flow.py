"""Upload → convert → outbox → download, driven by the stub engine (User Story 1)."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def test_a_document_moves_queued_then_converting_then_converted(
    upload, client, dispatcher, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(polls_pending=1, polls_running=1)
    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]

    async def status() -> str:
        return (await client.get(f"/api/jobs/{job_id}")).json()["status"]

    assert await status() == "queued"
    await dispatcher.run_once()
    assert await status() == "submitted"
    await dispatcher.run_once()  # the engine still reports the task as pending
    assert await status() == "submitted"
    await dispatcher.run_once()
    assert await status() == "running"
    await dispatcher.drain()
    assert await status() == "succeeded"


async def test_markdown_lands_in_the_outbox_and_downloads_under_the_same_name(
    convert, client, storage
):
    body = (await convert(("Annual Report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    detail = (await client.get(f"/api/jobs/{job_id}")).json()

    outbox_file = storage.outbox_file(detail["output_filename"])
    assert outbox_file.is_file()
    assert outbox_file.name.startswith("annual-report--")

    download = await client.get(f"/api/jobs/{job_id}/markdown")
    assert download.text == outbox_file.read_text()
    assert detail["output_filename"] in download.headers["content-disposition"]


async def test_queue_position_is_surfaced_while_queued(upload, client, dispatcher, stub_engine):
    stub_engine.default_behavior = TaskBehavior(polls_pending=2, polls_running=1)
    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    await dispatcher.run_once()
    await dispatcher.run_once()
    assert (await client.get(f"/api/jobs/{job_id}")).json()["queue_position"] is not None


async def test_started_and_ended_times_are_recorded(convert, client):
    body = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()
    assert detail["created_at"] and detail["started_at"] and detail["ended_at"]
    assert detail["page_count"] is None or detail["page_count"] > 0


async def test_partial_success_is_recorded_distinctly(convert, client, stub_engine):
    stub_engine.default_behavior = TaskBehavior(
        result_status="partial_success", errors=["Page 3: table structure could not be resolved"]
    )
    body = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()
    assert detail["status"] == "succeeded"
    assert detail["engine_status"] == "partial_success"
    assert detail["engine_errors"]


async def test_a_lost_result_fails_the_job_rather_than_leaving_it_running(
    convert, client, stub_engine, storage
):
    stub_engine.default_behavior = TaskBehavior(result_http_error=500)
    body = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()
    assert detail["status"] == "failed"
    assert detail["failure_reason"]
    assert list(storage.outbox_path.glob("*.md")) == []
