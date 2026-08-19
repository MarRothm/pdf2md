"""One document, one output file, whatever it is called (FR-014, User Story 4)."""

import pytest

from tests.conftest import pdf_bytes

pytestmark = pytest.mark.integration

DOCUMENT = pdf_bytes(b"the same bytes every time")


async def test_converting_identical_bytes_twice_yields_one_outbox_file(
    convert, client, storage, stub_engine
):
    first = (await convert(("report.pdf", DOCUMENT))).json()["accepted"][0]
    second = (await convert(("report.pdf", DOCUMENT))).json()["accepted"][0]

    assert first["status"] == "queued"
    assert second["status"] == "already_converted"
    assert (
        second["output_filename"]
        == (await client.get(f"/api/jobs/{first['job_id']}")).json()["output_filename"]
    )
    assert len(list(storage.outbox_path.glob("*.md"))) == 1
    assert len(stub_engine.submissions) == 1


async def test_a_renamed_copy_resolves_to_the_same_output(convert, client, storage):
    await convert(("report.pdf", DOCUMENT))
    renamed = (await convert(("report (copy).pdf", DOCUMENT))).json()["accepted"][0]

    assert renamed["status"] == "already_converted"
    # The slug follows the first filename seen; identity is the content hash.
    assert renamed["output_filename"].startswith("report--")
    assert len(list(storage.outbox_path.glob("*.md"))) == 1


async def test_the_second_job_is_downloadable_without_being_converted_again(convert, client):
    await convert(("report.pdf", DOCUMENT))
    second = (await convert(("report.pdf", DOCUMENT))).json()["accepted"][0]

    detail = (await client.get(f"/api/jobs/{second['job_id']}")).json()
    assert detail["display_status"] == "Already converted"
    assert detail["download_url"]
    assert (await client.get(detail["download_url"])).status_code == 200


async def test_dedup_only_applies_while_the_output_file_is_still_there(
    convert, client, storage, stub_engine
):
    first = (await convert(("report.pdf", DOCUMENT))).json()["accepted"][0]
    output_filename = (await client.get(f"/api/jobs/{first['job_id']}")).json()["output_filename"]
    storage.outbox_file(output_filename).unlink()

    again = (await convert(("report.pdf", DOCUMENT))).json()["accepted"][0]
    assert again["status"] == "queued"
    assert len(stub_engine.submissions) == 2
    assert storage.outbox_file(output_filename).is_file()


async def test_different_documents_never_share_an_output_file(convert, storage):
    await convert(("a.pdf", pdf_bytes(b"document a")), ("b.pdf", pdf_bytes(b"document b")))
    assert len(list(storage.outbox_path.glob("*.md"))) == 2
