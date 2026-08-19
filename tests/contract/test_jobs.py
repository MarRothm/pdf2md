"""`GET /api/jobs`, `GET /api/jobs/{id}`, and the Markdown download."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.contract

SUMMARY_FIELDS = {
    "job_id",
    "batch_id",
    "filename",
    "status",
    "display_status",
    "queue_position",
    "created_at",
    "started_at",
    "ended_at",
    "attempt",
    "size_bytes",
    "page_count",
    "failure_reason",
    "output_filename",
    "download_url",
}


async def test_job_list_shape(upload, client):
    await upload(("report.pdf", pdf_bytes(b"a")))
    body = (await client.get("/api/jobs")).json()
    assert set(body) == {"server_time", "backlog", "jobs"}
    assert body["server_time"].endswith("Z")
    assert set(body["backlog"]) == {"queued", "converting"}
    assert body["backlog"]["queued"] == 1
    assert set(body["jobs"][0]) >= SUMMARY_FIELDS


async def test_display_status_is_derived_by_the_server(convert, client, stub_engine):
    stub_engine.set_behavior("empty.pdf", TaskBehavior(markdown="", result_status="success"))
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    await convert(
        ("report.pdf", pdf_bytes(b"a")),
        ("empty.pdf", pdf_bytes(b"b")),
        ("broken.pdf", pdf_bytes(b"c")),
    )
    jobs = {job["filename"]: job for job in (await client.get("/api/jobs")).json()["jobs"]}
    assert jobs["report.pdf"]["display_status"] == "Converted"
    assert jobs["empty.pdf"]["display_status"] == "Converted — check output"
    assert jobs["broken.pdf"]["display_status"] == "Failed"


async def test_download_url_is_present_for_exactly_the_successful_jobs(
    convert, client, stub_engine
):
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    await convert(("report.pdf", pdf_bytes(b"a")), ("broken.pdf", pdf_bytes(b"c")))
    jobs = {job["filename"]: job for job in (await client.get("/api/jobs")).json()["jobs"]}
    assert jobs["report.pdf"]["download_url"] == (
        f"/api/jobs/{jobs['report.pdf']['job_id']}/markdown"
    )
    assert jobs["broken.pdf"]["download_url"] is None
    assert jobs["broken.pdf"]["failure_reason"]


async def test_job_detail_adds_engine_fields(convert, client):
    body = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert set(detail) >= SUMMARY_FIELDS
    assert detail["engine_status"] == "success"
    assert detail["content_hash"]
    assert detail["output_bytes"] > 0
    assert detail["processing_seconds"] is not None


async def test_unknown_job_is_404_with_the_error_shape(client):
    response = await client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message"}


async def test_markdown_download_headers_match_the_outbox_filename(convert, client):
    body = (await convert(("Annual Report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    output_filename = (await client.get(f"/api/jobs/{job_id}")).json()["output_filename"]
    response = await client.get(f"/api/jobs/{job_id}/markdown")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (f'attachment; filename="{output_filename}"')
    assert response.text.startswith("# Converted document")


async def test_download_is_409_while_the_document_is_still_converting(upload, client):
    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    response = await client.get(f"/api/jobs/{job_id}/markdown")
    assert response.status_code == 409
    assert "converting" in response.json()["error"]["message"].lower()


async def test_download_is_404_for_a_failed_job(convert, client, stub_engine):
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    body = (await convert(("broken.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    assert (await client.get(f"/api/jobs/{job_id}/markdown")).status_code == 404


async def test_list_filters_by_batch_status_and_limit(convert, client, stub_engine):
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    first = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    second = (await convert(("broken.pdf", pdf_bytes(b"b")))).json()

    by_batch = (await client.get(f"/api/jobs?batch_id={second['batch_id']}")).json()["jobs"]
    assert [job["filename"] for job in by_batch] == ["broken.pdf"]

    by_status = (await client.get("/api/jobs?status=succeeded")).json()["jobs"]
    assert [job["batch_id"] for job in by_status] == [first["batch_id"]]

    assert len((await client.get("/api/jobs?limit=1")).json()["jobs"]) == 1


async def test_since_returns_only_jobs_changed_after_the_marker(upload, client, dispatcher):
    await upload(("first.pdf", pdf_bytes(b"a")))
    marker = (await client.get("/api/jobs")).json()["server_time"]
    await upload(("second.pdf", pdf_bytes(b"b")))
    changed = (await client.get(f"/api/jobs?since={marker}")).json()["jobs"]
    assert [job["filename"] for job in changed] == ["second.pdf"]
