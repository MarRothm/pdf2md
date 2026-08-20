"""`DELETE /api/jobs/{id}` and the payload additions the confirmation depends on.

Contract: `specs/002-job-list-layout-delete/contracts/web-api-deletion.md`.
"""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.contract

DELETION_FIELDS = {"job_ids", "filename", "removed_files", "kept_files", "upload_discarded"}


# --- the payload additions --------------------------------------------------


async def test_every_summary_carries_the_content_hash(upload, client):
    await upload(("report.pdf", pdf_bytes(b"a")))
    body = (await client.get("/api/jobs")).json()
    assert len(body["jobs"][0]["content_hash"]) == 64


async def test_the_list_can_be_filtered_by_content_hash(convert, client):
    same = pdf_bytes(b"same")
    await convert(("report.pdf", same))
    await convert(("report.pdf", same))
    await convert(("other.pdf", pdf_bytes(b"other")))

    content_hash = (await client.get("/api/jobs")).json()["jobs"][0]["content_hash"]
    filtered = (await client.get(f"/api/jobs?content_hash={content_hash}")).json()["jobs"]

    assert {job["content_hash"] for job in filtered} == {content_hash}


async def test_an_unknown_content_hash_returns_an_empty_list(client):
    body = (await client.get(f"/api/jobs?content_hash={'f' * 64}")).json()
    assert body["jobs"] == []


async def test_detail_reports_the_documents_files_and_its_retained_upload(convert, client):
    response = await convert(("report.pdf", pdf_bytes(b"a")))
    detail = (await client.get(f"/api/jobs/{response.json()['accepted'][0]['job_id']}")).json()

    assert [output["filename"] for output in detail["document_outputs"]] == [
        output["filename"] for output in detail["outputs"]
    ]
    assert detail["retained_upload"] is True


async def test_an_already_converted_job_reports_the_documents_files_not_its_own(convert, client):
    """The case a confirmation built from `outputs` would get catastrophically wrong.

    `markdown_output.job_id` names the job that wrote the file, so an `already_converted`
    job's own `outputs` is empty while the document it points at has files. A dialog built
    from `outputs` would promise to remove nothing, then remove every section file.
    """
    same = pdf_bytes(b"identical")
    await convert(("report.pdf", same))
    second = (await convert(("report.pdf", same))).json()["accepted"][0]
    assert second["status"] == "already_converted"

    detail = (await client.get(f"/api/jobs/{second['job_id']}")).json()
    assert detail["outputs"] == []
    assert len(detail["document_outputs"]) == 1


# --- DELETE -----------------------------------------------------------------


async def test_delete_returns_what_it_removed(converted_document, client):
    document = await converted_document()
    body = (await client.delete(f"/api/jobs/{document.job_id}")).json()

    assert set(body) == DELETION_FIELDS
    assert body["job_ids"] == [document.job_id]
    assert body["filename"] == "report.pdf"
    assert body["removed_files"] == document.filenames
    assert body["kept_files"] == []
    assert body["upload_discarded"] is True


async def test_delete_reports_every_entry_of_the_document(convert, client):
    same = pdf_bytes(b"identical")
    first = (await convert(("report.pdf", same))).json()["accepted"][0]["job_id"]
    second = (await convert(("report.pdf", same))).json()["accepted"][0]["job_id"]

    body = (await client.delete(f"/api/jobs/{second}")).json()
    assert set(body["job_ids"]) == {first, second}


async def test_deleting_a_conversion_still_in_flight_is_refused(upload, client):
    job_id = (await upload(("report.pdf", pdf_bytes(b"a")))).json()["accepted"][0]["job_id"]

    response = await client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "still_converting"
    assert "report.pdf" in response.json()["error"]["message"]


async def test_deleting_twice_reports_the_second_as_already_deleted(converted_document, client):
    document = await converted_document()
    assert (await client.delete(f"/api/jobs/{document.job_id}")).status_code == 200

    response = await client.delete(f"/api/jobs/{document.job_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "already_deleted"


async def test_a_conversion_that_produced_nothing_still_deletes(convert, client, stub_engine):
    stub_engine.set_behavior("broken.pdf", TaskBehavior(task_status_on_finish="failure"))
    job_id = (await convert(("broken.pdf", pdf_bytes(b"b")))).json()["accepted"][0]["job_id"]

    body = (await client.delete(f"/api/jobs/{job_id}")).json()
    assert body["removed_files"] == []
    assert body["job_ids"] == [job_id]
