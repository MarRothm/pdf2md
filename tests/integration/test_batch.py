"""A mixed batch runs unattended to a definite outcome per document (User Story 5)."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def test_one_failure_does_not_block_the_rest_of_the_batch(
    convert, client, storage, stub_engine
):
    stub_engine.set_behavior("bad-3.pdf", TaskBehavior(task_status_on_finish="failure"))
    documents = [(f"doc-{index}.pdf", pdf_bytes(f"body {index}".encode())) for index in range(6)]
    documents[3] = ("bad-3.pdf", pdf_bytes(b"bad"))

    body = (await convert(*documents)).json()
    assert len(body["accepted"]) == 6

    jobs = {job["filename"]: job for job in (await client.get("/api/jobs?limit=50")).json()["jobs"]}
    assert jobs["bad-3.pdf"]["status"] == "failed"
    assert jobs["bad-3.pdf"]["failure_reason"]
    assert all(
        jobs[f"doc-{index}.pdf"]["status"] == "succeeded" for index in range(6) if index != 3
    )
    assert len(list(storage.outbox_path.glob("*.md"))) == 5


async def test_every_document_in_a_large_batch_reaches_a_terminal_state(convert, client, storage):
    documents = [(f"doc-{index}.pdf", pdf_bytes(f"body {index}".encode())) for index in range(50)]
    body = (await convert(*documents)).json()
    assert len(body["accepted"]) == 50

    listing = (await client.get("/api/jobs?limit=500")).json()
    assert listing["backlog"] == {"queued": 0, "converting": 0}
    assert len(listing["jobs"]) == 50
    assert {job["status"] for job in listing["jobs"]} == {"succeeded"}
    assert len(list(storage.outbox_path.glob("*.md"))) == 50


async def test_in_flight_work_is_bounded_by_the_engine_worker_count(
    upload, dispatcher, db, settings, stub_engine
):
    stub_engine.default_behavior = TaskBehavior(polls_pending=2, polls_running=2)
    documents = [(f"doc-{index}.pdf", pdf_bytes(f"body {index}".encode())) for index in range(20)]
    await upload(*documents)

    for _ in range(5):
        await dispatcher.run_once()
        assert db.count_active() <= settings.max_in_flight

    assert db.backlog().queued > 0  # the rest queue rather than flooding the engine


async def test_a_batch_is_reported_as_a_group(convert, client):
    body = (await convert(("a.pdf", pdf_bytes(b"a")), ("b.pdf", pdf_bytes(b"b")))).json()
    batch_id = body["batch_id"]
    jobs = (await client.get(f"/api/jobs?batch_id={batch_id}")).json()["jobs"]
    assert len(jobs) == 2
    assert {job["batch_id"] for job in jobs} == {batch_id}


async def test_rejected_files_are_reported_without_failing_the_batch(convert, client, storage):
    body = (
        await convert(
            ("good.pdf", pdf_bytes(b"good")),
            ("notes.txt", b"not a pdf"),
            ("empty.pdf", b""),
        )
    ).json()
    assert len(body["accepted"]) == 1
    assert len(body["rejected"]) == 2
    assert len(list(storage.outbox_path.glob("*.md"))) == 1
