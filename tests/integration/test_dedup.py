"""One document, one output file, whatever it is called (FR-014, User Story 4)."""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

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


async def test_an_incomplete_conversion_is_not_offered_as_a_finished_one(
    convert, upload, client, dispatcher, stub_engine, settings
):
    """A file with gaps in it does not answer a request to convert the document (FR-040).

    Otherwise every re-upload hands back the same holed Markdown, and the only way to ask
    for a whole document is to delete this one — which throws away what did convert.
    """
    settings.part_max_pages = 10
    settings.part_min_pages = 10  # at the floor, so the gap is reported rather than halved
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    stub_engine.set_behavior(
        "holed.pdf (pages 1-10)", TaskBehavior(task_status_on_finish="failure")
    )
    document = ("holed.pdf", pdf_bytes(b"holed", pages=25))

    first = await _detail(client, await convert(document))
    assert first["status"] == "succeeded_incomplete"

    # the same document again: converted afresh, not answered with the holed file
    stub_engine.behaviors.clear()
    second = (await upload(document)).json()["accepted"][0]
    assert second["status"] == "queued"

    await dispatcher.drain()
    again = (await client.get(f"/api/jobs/{second['job_id']}")).json()
    assert again["status"] == "succeeded"
    assert again["missing_page_ranges"] is None


async def test_an_incomplete_document_can_be_converted_again(
    convert, client, dispatcher, stub_engine, settings
):
    """`Convert again` on the page, and the retry endpoint behind it."""
    settings.part_max_pages = 10
    settings.part_min_pages = 10
    stub_engine.default_behavior = TaskBehavior(markdown="body " * 200)
    stub_engine.set_behavior(
        "gappy.pdf (pages 1-10)", TaskBehavior(task_status_on_finish="failure")
    )

    incomplete = await _detail(client, await convert(("gappy.pdf", pdf_bytes(b"gappy", pages=25))))
    assert incomplete["status"] == "succeeded_incomplete"

    stub_engine.behaviors.clear()
    response = await client.post(f"/api/jobs/{incomplete['job_id']}/retry")
    assert response.status_code == 202

    await dispatcher.drain()
    retried = (await client.get(f"/api/jobs/{response.json()['job_id']}")).json()
    assert retried["status"] == "succeeded"


async def _detail(client, response):
    return (await client.get(f"/api/jobs/{response.json()['accepted'][0]['job_id']}")).json()
