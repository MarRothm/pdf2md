"""Corrupt and unreadable documents end failed, with nothing in the outbox (FR-007)."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


async def test_a_corrupt_pdf_fails_with_a_readable_reason_and_no_output(
    convert, client, storage, stub_engine
):
    stub_engine.set_behavior(
        "corrupt.pdf",
        TaskBehavior(
            task_status_on_finish="failure",
            errors=["Traceback (most recent call last): pypdfium2._helpers.misc.PdfiumError"],
        ),
    )
    body = (await convert(("corrupt.pdf", pdf_bytes(b"garbage")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()

    assert detail["status"] == "failed"
    assert "Traceback" not in detail["failure_reason"]
    assert len(detail["failure_reason"]) > 10
    assert detail["download_url"] is None
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_an_engine_reported_skip_fails_the_job(convert, client, stub_engine, storage):
    stub_engine.default_behavior = TaskBehavior(result_status="skipped", errors=["unsupported"])
    body = (await convert(("odd.pdf", pdf_bytes(b"odd")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()
    assert detail["status"] == "failed"
    assert detail["failure_reason"]
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_a_password_protected_pdf_is_stopped_at_upload(upload, storage):
    response = await upload(("locked.pdf", pdf_bytes(b"x", encrypted=True)))
    assert response.json()["accepted"] == []
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_a_bad_document_does_not_stop_the_good_one_beside_it(
    convert, client, storage, stub_engine
):
    stub_engine.set_behavior("corrupt.pdf", TaskBehavior(task_status_on_finish="failure"))
    await convert(("good.pdf", pdf_bytes(b"good")), ("corrupt.pdf", pdf_bytes(b"bad")))
    jobs = {job["filename"]: job for job in (await client.get("/api/jobs")).json()["jobs"]}
    assert jobs["good.pdf"]["status"] == "succeeded"
    assert jobs["corrupt.pdf"]["status"] == "failed"
    assert len(list(storage.outbox_path.glob("*.md"))) == 1


async def test_a_page_limit_failure_says_so_rather_than_blaming_the_file(
    convert, client, storage, stub_engine
):
    """The engine's reason has to reach the message, not just the log (FR-011).

    A document over `DOCLING_SERVE_MAX_NUM_PAGES` is not damaged, and telling its owner
    to re-upload it wastes an hour of their time for a document that can never succeed.
    Regression test: the poll-failure path used to derive the message from the word
    "failure" alone, so every engine failure read as "the PDF is probably damaged".
    """
    stub_engine.set_behavior(
        "huge.pdf",
        TaskBehavior(
            task_status_on_finish="failure",
            errors=["Document has too many pages (2413 > 2000)"],
        ),
    )
    body = (await convert(("huge.pdf", pdf_bytes(b"many pages")))).json()
    detail = (await client.get(f"/api/jobs/{body['accepted'][0]['job_id']}")).json()

    assert detail["status"] == "failed"
    assert "larger than the converter accepts" in detail["failure_reason"]
    assert "damaged" not in detail["failure_reason"]
    assert "2413" not in detail["failure_reason"], "engine detail belongs in engine_errors"
    assert detail["engine_errors"] == ["Document has too many pages (2413 > 2000)"]
    assert list(storage.outbox_path.glob("*.md")) == []
