"""`POST /api/uploads` against contracts/web-api.md (FR-007, FR-008, FR-009)."""

import pytest

from tests.conftest import pdf_bytes

pytestmark = pytest.mark.contract


async def test_accepted_upload_returns_the_202_shape(upload):
    response = await upload(("annual-report.pdf", pdf_bytes(b"a")), note="Q3 pack")
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"batch_id", "accepted", "rejected"}
    assert body["batch_id"]
    assert body["rejected"] == []
    (accepted,) = body["accepted"]
    assert set(accepted) >= {"job_id", "filename", "status"}
    assert accepted["filename"] == "annual-report.pdf"
    assert accepted["status"] == "queued"


async def test_a_rejected_file_does_not_fail_the_batch(upload):
    response = await upload(
        ("good.pdf", pdf_bytes(b"good")),
        ("notes.txt", b"just some notes"),
    )
    assert response.status_code == 202
    body = response.json()
    assert [item["filename"] for item in body["accepted"]] == ["good.pdf"]
    (rejected,) = body["rejected"]
    assert rejected["filename"] == "notes.txt"
    assert "PDF" in rejected["reason"]


async def test_non_pdf_zero_byte_and_oversized_files_are_each_rejected(upload, settings):
    settings.max_upload_bytes = 200
    response = await upload(
        ("notes.txt", b"not a pdf at all"),
        ("empty.pdf", b""),
        ("huge.pdf", pdf_bytes(b"x" * 400)),
    )
    assert response.status_code == 202
    reasons = {item["filename"]: item["reason"] for item in response.json()["rejected"]}
    assert set(reasons) == {"notes.txt", "empty.pdf", "huge.pdf"}
    assert "PDF" in reasons["notes.txt"]
    assert "empty" in reasons["empty.pdf"].lower()
    assert "limit" in reasons["huge.pdf"].lower()
    assert response.json()["accepted"] == []


async def test_a_password_protected_pdf_is_rejected_with_a_plain_reason(upload):
    response = await upload(("locked.pdf", pdf_bytes(b"secret", encrypted=True)))
    (rejected,) = response.json()["rejected"]
    assert "password" in rejected["reason"].lower()


async def test_rejections_never_create_a_job(upload, client):
    await upload(("notes.txt", b"not a pdf"))
    jobs = (await client.get("/api/jobs")).json()["jobs"]
    assert jobs == []


async def test_an_upload_with_no_files_is_a_client_error(client):
    response = await client.post("/api/uploads", files=[])
    assert response.status_code == 422
    assert "error" in response.json()


async def test_a_job_is_listed_immediately_after_the_upload_returns(upload, client):
    body = (await upload(("report.pdf", pdf_bytes(b"r")))).json()
    listed = (await client.get("/api/jobs")).json()["jobs"]
    assert [job["job_id"] for job in listed] == [body["accepted"][0]["job_id"]]
    assert listed[0]["display_status"] == "Queued"


async def test_the_batch_note_is_optional(upload):
    assert (await upload(("report.pdf", pdf_bytes(b"n")))).status_code == 202
