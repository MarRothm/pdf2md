"""Deleting a document: what leaves, what stays, and what a re-upload does afterwards.

Feature 002, User Story 2. The unit of deletion is the source document, not the job —
conversions of one PDF share its output and its retained upload, so they go together
(FR-021).
"""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


def _markdown(*titles: str, body: str = "x" * 30_000) -> str:
    return "".join(f"# {title}\n\n{body}\n\n" for title in titles)


async def test_every_section_file_of_a_split_document_is_removed(
    convert, client, storage, stub_engine, settings
):
    settings.section_split_threshold_bytes = 1000
    settings.section_min_bytes = 100
    settings.section_max_bytes = 10**6
    stub_engine.default_behavior = TaskBehavior(markdown=_markdown("Alpha", "Beta", "Gamma"))

    job_id = (await convert(("manual.pdf", pdf_bytes(b"m")))).json()["accepted"][0]["job_id"]
    assert len(list(storage.outbox_path.glob("*.md"))) == 3

    body = (await client.delete(f"/api/jobs/{job_id}")).json()

    assert len(body["removed_files"]) == 3
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_another_documents_output_is_untouched(convert, client, storage):
    keep = (await convert(("keep.pdf", pdf_bytes(b"keep")))).json()["accepted"][0]
    drop = (await convert(("drop.pdf", pdf_bytes(b"drop")))).json()["accepted"][0]

    await client.delete(f"/api/jobs/{drop['job_id']}")

    remaining = [path.name for path in storage.outbox_path.glob("*.md")]
    assert remaining == [
        (await client.get(f"/api/jobs/{keep['job_id']}")).json()["output_filename"]
    ]


async def test_both_entries_of_a_twice_converted_document_go_together(convert, client, storage):
    same = pdf_bytes(b"identical")
    first = (await convert(("report.pdf", same))).json()["accepted"][0]["job_id"]
    second = (await convert(("report.pdf", same))).json()["accepted"][0]["job_id"]

    await client.delete(f"/api/jobs/{first}")

    jobs = (await client.get("/api/jobs")).json()["jobs"]
    assert [job["job_id"] for job in jobs if job["job_id"] in {first, second}] == []
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_the_retained_upload_is_discarded(convert, client, storage):
    response = await convert(("report.pdf", pdf_bytes(b"a")))
    job_id = response.json()["accepted"][0]["job_id"]
    content_hash = (await client.get(f"/api/jobs/{job_id}")).json()["content_hash"]
    assert storage.has_inbox_file(content_hash)

    await client.delete(f"/api/jobs/{job_id}")
    assert not storage.has_inbox_file(content_hash)


async def test_re_uploading_a_deleted_document_converts_it_again(
    convert, client, storage, stub_engine
):
    """The point of removing the source_document row (FR-023)."""
    same = pdf_bytes(b"identical")
    first = (await convert(("report.pdf", same))).json()["accepted"][0]["job_id"]
    await client.delete(f"/api/jobs/{first}")

    again = (await convert(("report.pdf", same))).json()["accepted"][0]
    assert again["status"] == "queued"
    assert len(stub_engine.submissions) == 2
    assert len(list(storage.outbox_path.glob("*.md"))) == 1


async def test_deleting_when_the_markdown_is_already_gone_still_succeeds(
    converted_document, client, storage
):
    document = await converted_document()
    for name in document.filenames:
        storage.outbox_file(name).unlink()

    response = await client.delete(f"/api/jobs/{document.job_id}")
    assert response.status_code == 200
    assert response.json()["removed_files"] == []
    assert (await client.get("/api/jobs")).json()["jobs"] == []


async def test_an_unwritable_outbox_keeps_the_files_and_still_clears_the_records(
    converted_document, client, storage, monkeypatch
):
    document = await converted_document()

    def refuse(self, output_filename):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(storage), "delete_outbox_file", refuse)

    body = (await client.delete(f"/api/jobs/{document.job_id}")).json()

    assert body["kept_files"] == document.filenames
    assert body["removed_files"] == []
    assert (await client.get("/api/jobs")).json()["jobs"] == []
    assert all(storage.outbox_file(name).is_file() for name in document.filenames)


async def test_the_outbox_count_drops_immediately(converted_document, client):
    document = await converted_document()
    before = (await client.get("/api/health")).json()["outbox"]["documents"]

    await client.delete(f"/api/jobs/{document.job_id}")

    after = (await client.get("/api/health")).json()["outbox"]["documents"]
    assert (before, after) == (1, 0)


async def test_a_document_with_a_retry_in_flight_is_protected(
    convert, upload, client, db, stub_engine
):
    """FR-022: the sibling the operator is not looking at still protects the document."""
    stub_engine.set_behavior("report.pdf", TaskBehavior(task_status_on_finish="failure"))
    failed = (await convert(("report.pdf", pdf_bytes(b"a")))).json()["accepted"][0]["job_id"]

    retry = (await client.post(f"/api/jobs/{failed}/retry")).json()
    assert retry["status"] == "queued"

    response = await client.delete(f"/api/jobs/{failed}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "still_converting"
