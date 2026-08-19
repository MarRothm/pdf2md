"""What reaches the outbox, and what must never reach it (FR-007, FR-013)."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def test_no_file_appears_for_a_failed_job(convert, client, storage, stub_engine):
    stub_engine.default_behavior = TaskBehavior(task_status_on_finish="failure")
    body = (await convert(("broken.pdf", pdf_bytes(b"a")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()

    assert detail["status"] == "failed"
    assert list(storage.outbox_path.iterdir()) == []


async def test_an_interrupted_write_leaves_no_truncated_markdown(
    upload, dispatcher, client, storage, monkeypatch
):
    import os

    body = (await upload(("report.pdf", pdf_bytes(b"a")))).json()

    def explode(source, destination):
        raise OSError("interrupted mid-write")

    monkeypatch.setattr(os, "replace", explode)  # the rename that makes a write visible
    await dispatcher.drain()
    monkeypatch.undo()

    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()

    assert detail["status"] == "failed"
    assert "convert it again" in detail["failure_reason"].lower()
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_the_download_filename_is_the_outbox_filename(convert, client, storage):
    body = (await convert(("Quarterly Review.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    detail = (await client.get(f"/api/jobs/{job_id}")).json()

    (outbox_file,) = list(storage.outbox_path.glob("*.md"))
    assert detail["output_filename"] == outbox_file.name
    response = await client.get(f"/api/jobs/{job_id}/markdown")
    assert response.headers["content-disposition"] == f'attachment; filename="{outbox_file.name}"'


async def test_a_removed_output_is_reported_differently_from_one_never_produced(
    convert, client, storage, stub_engine
):
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    good = (await convert(("good.pdf", pdf_bytes(b"a")))).json()["accepted"][0]
    bad = (await convert(("broken.pdf", pdf_bytes(b"b")))).json()["accepted"][0]

    output_filename = (await client.get(f"/api/jobs/{good['job_id']}")).json()["output_filename"]
    storage.outbox_file(output_filename).unlink()

    removed = await client.get(f"/api/jobs/{good['job_id']}/markdown")
    never = await client.get(f"/api/jobs/{bad['job_id']}/markdown")

    assert removed.status_code == never.status_code == 404
    assert removed.json()["error"]["code"] == "output_removed"
    assert never.json()["error"]["code"] == "no_output"
    assert "removed" in removed.json()["error"]["message"].lower()


async def test_a_retry_produces_a_new_job_against_the_same_document(
    convert, client, storage, stub_engine, dispatcher
):
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    failed = (await convert(("broken.pdf", pdf_bytes(b"a")))).json()["accepted"][0]

    stub_engine.behaviors.clear()
    retry = await client.post(f"/api/jobs/{failed['job_id']}/retry")
    assert retry.status_code == 202
    retry_id = retry.json()["job_id"]
    assert retry_id != failed["job_id"]

    await dispatcher.drain()

    detail = (await client.get(f"/api/jobs/{retry_id}")).json()
    assert detail["status"] == "succeeded"
    assert len(list(storage.outbox_path.glob("*.md"))) == 1
    assert (await client.get(f"/api/jobs/{failed['job_id']}")).json()["status"] == "failed"


async def test_retry_is_refused_for_a_document_that_already_converted(convert, client):
    body = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    response = await client.post(f"/api/jobs/{body['accepted'][0]['job_id']}/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_converted"


async def test_retry_is_refused_once_the_upload_has_been_reaped(
    convert, client, storage, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(task_status_on_finish="failure")
    body = (await convert(("broken.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    content_hash = (await client.get(f"/api/jobs/{job_id}")).json()["content_hash"]
    storage.delete_inbox_file(content_hash)

    response = await client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "upload_gone"
    assert "upload the document again" in response.json()["error"]["message"].lower()
