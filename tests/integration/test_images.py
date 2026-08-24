"""Pictures as files, end to end (feature 003).

Quickstart V1 and V2. The Markdown that reaches the outbox must contain no picture data —
AnythingLLM cannot ingest a document that does — and every reference in it must name a file
that is actually there.
"""

import io
import re
import zipfile

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import StubPicture, TaskBehavior

pytestmark = pytest.mark.integration

BODY = "# Contract\n\n" + "clause " * 200


def _distinct(index: object, **kwargs) -> StubPicture:
    """A picture no other picture is byte-identical to.

    Identical pictures are now one file with many references, so a test that means
    *several figures* has to say so — and one that means *the same letterhead again* can
    now say that too.
    """
    return StubPicture(payload=StubPicture().payload + str(index).encode(), **kwargs)


def _outbox_markdown(storage):
    return next(iter(storage.outbox_path.glob("*.md"))).read_text()


async def _detail(client, response):
    return (await client.get(f"/api/jobs/{response.json()['accepted'][0]['job_id']}")).json()


async def test_markdown_has_no_picture_data(convert, client, storage, stub_engine, settings):
    """FR-001 — the requirement the knowledge base actually depends on."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[StubPicture()])

    await convert(("figures.pdf", pdf_bytes(b"fig")))

    text = _outbox_markdown(storage)
    assert "data:image" not in text
    assert "base64" not in text
    assert "<!-- image -->" not in text


async def test_references_resolve_to_files_on_disk(convert, client, storage, stub_engine, settings):
    """FR-003 — read back from the folder, not from the string we wrote."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(
        markdown=BODY, pictures=[_distinct(1), _distinct(2)]
    )

    detail = await _detail(client, await convert(("figures.pdf", pdf_bytes(b"two"))))

    text = _outbox_markdown(storage)
    referenced = re.findall(r"!\[\]\(([^)]+)\)", text)
    assert len(referenced) == 2
    for name in referenced:
        assert (storage.outbox_path / name).is_file()
    assert detail["image_count"] == 2


async def test_a_page_sized_image_produces_no_file_and_no_marker(
    convert, client, storage, stub_engine, settings
):
    """Quickstart V3, FR-004: a scanned page is its recognised text."""
    settings.extract_images = True
    whole_page = StubPicture(bbox=(0.0, 842.0, 595.0, 0.0))
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[whole_page])

    detail = await _detail(client, await convert(("scan.pdf", pdf_bytes(b"scan"))))

    text = _outbox_markdown(storage)
    assert list(storage.outbox_path.glob("*.png")) == []
    assert "![](" not in text
    assert "picture" not in text.lower()
    assert detail["image_count"] == 0


async def test_a_figure_filling_most_of_a_page_is_still_a_figure(
    convert, storage, stub_engine, settings
):
    """The other side of the 0.8 boundary, at document level."""
    settings.extract_images = True
    tall = StubPicture(bbox=(0.0, 842.0 * 0.79, 595.0, 0.0))
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[tall])

    await convert(("figure.pdf", pdf_bytes(b"tall")))

    assert len(list(storage.outbox_path.glob("*.png"))) == 1


async def test_a_skipped_picture_leaves_nothing(convert, storage, stub_engine, settings):
    """Quickstart V4, FR-006 as revised: the Markdown carries a reference or nothing."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(
        markdown=BODY, pictures=[StubPicture(payload=b"tiny")]
    )

    await convert(("small.pdf", pdf_bytes(b"small")))

    text = _outbox_markdown(storage)
    assert list(storage.outbox_path.glob("*.png")) == []
    assert "picture" not in text.lower()
    assert "data:image" not in text


async def test_extraction_off_writes_no_files_and_no_picture_data(
    convert, storage, stub_engine, settings
):
    """FR-010 — and off is still not the old behaviour."""
    settings.extract_images = False
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[StubPicture()])

    await convert(("off.pdf", pdf_bytes(b"off")))

    assert list(storage.outbox_path.glob("*.png")) == []
    assert "data:image" not in _outbox_markdown(storage)


async def test_a_document_with_no_pictures_is_untouched(convert, storage, stub_engine, settings):
    """SC-006 — same Markdown, same file count, no image files."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(markdown=BODY)

    await convert(("plain.pdf", pdf_bytes(b"plain")))

    assert list(storage.outbox_path.glob("*.png")) == []
    assert _outbox_markdown(storage).startswith("# Contract")


async def test_re_conversion_leaves_no_orphaned_pictures(
    convert, storage, stub_engine, settings, db, dispatcher
):
    """A second conversion finding fewer pictures must not leave the extra files for ever.

    Driven through `persist_markdown` rather than the retry endpoint, which refuses a job
    that already succeeded — correctly, and for reasons that predate this feature.
    """
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(
        markdown=BODY, pictures=[_distinct(1), _distinct(2)]
    )
    response = await convert(("again.pdf", pdf_bytes(b"again")))
    assert len(list(storage.outbox_path.glob("*.png"))) == 2

    job = db.get_job(response.json()["accepted"][0]["job_id"])
    one_picture = {
        "pictures": [StubPicture().as_json()],
        "pages": {"1": {"size": {"width": 595.0, "height": 842.0}, "page_no": 1}},
    }
    dispatcher.persist_markdown(
        job,
        BODY + "\n\n<!-- image -->",
        engine_status="success",
        document=one_picture,
    )

    assert len(list(storage.outbox_path.glob("*.png"))) == 1


async def test_pictures_are_numbered_once_across_a_split_document(
    convert, client, storage, stub_engine, settings
):
    """Quickstart V5. A part knows only its own pictures; numbering per part would restart
    at one every forty pages and two parts would fight over the same filename."""
    settings.extract_images = True
    settings.part_max_pages = 10
    for pages in ("1-10", "11-20", "21-25"):
        stub_engine.set_behavior(
            f"long.pdf (pages {pages})",
            TaskBehavior(markdown=BODY, pictures=[_distinct(f"{pages}a"), _distinct(f"{pages}b")]),
        )

    detail = await _detail(client, await convert(("long.pdf", pdf_bytes(b"split", pages=25))))

    assert detail["part_count"] == 3
    assert detail["image_count"] == 6

    text = _outbox_markdown(storage)
    referenced = re.findall(r"!\[\]\(([^)]+)\)", text)
    assert len(referenced) == 6
    assert referenced == sorted(referenced), "references must run in document order"
    assert len(set(referenced)) == 6, "no two parts may claim one filename"
    for name in referenced:
        assert (storage.outbox_path / name).is_file()
    assert "<!-- image -->" not in text


async def test_the_scratch_does_not_outlive_the_conversion(convert, storage, stub_engine, settings):
    settings.extract_images = True
    settings.part_max_pages = 10
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[StubPicture()])

    await convert(("long.pdf", pdf_bytes(b"scratch", pages=25)))

    assert list(storage.inbox_path.glob("*--part*")) == []


async def test_the_archive_carries_the_pictures(convert, client, storage, stub_engine, settings):
    """FR-009 — a reference has to resolve inside what the browser was handed, too."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[StubPicture()])

    detail = await _detail(client, await convert(("archive.pdf", pdf_bytes(b"arch"))))
    assert detail["download_all_url"], "one Markdown file plus a picture is still several files"

    archive = await client.get(detail["download_all_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        names = bundle.namelist()
        markdown = next(name for name in names if name.endswith(".md"))
        for reference in re.findall(r"!\[\]\(([^)]+)\)", bundle.read(markdown).decode()):
            assert reference in names


async def test_deleting_a_document_takes_its_pictures(
    convert, client, storage, stub_engine, settings
):
    """Quickstart V6, FR-008. An image left behind is exactly the orphan in the outbox
    that feature 002 was written to end."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[StubPicture()])

    response = await convert(("gone.pdf", pdf_bytes(b"gone")))
    job_id = response.json()["accepted"][0]["job_id"]
    assert len(list(storage.outbox_path.glob("*.png"))) == 1

    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    assert detail["image_count"] == 1  # the confirmation is built from this

    deleted = await client.delete(f"/api/jobs/{job_id}")
    assert deleted.status_code == 200
    assert any(name.endswith(".png") for name in deleted.json()["removed_files"])
    assert list(storage.outbox_path.glob("*.png")) == []
    assert list(storage.outbox_path.glob("*.md")) == []


async def test_deleting_one_document_leaves_another_document_pictures(
    convert, client, storage, stub_engine, settings
):
    """SC-005 — and the guarantee that nothing removes a file it cannot name."""
    settings.extract_images = True
    stub_engine.default_behavior = TaskBehavior(markdown=BODY, pictures=[StubPicture()])

    first = await convert(("one.pdf", pdf_bytes(b"one")))
    await convert(("two.pdf", pdf_bytes(b"two")))
    assert len(list(storage.outbox_path.glob("*.png"))) == 2

    await client.delete(f"/api/jobs/{first.json()['accepted'][0]['job_id']}")

    survivors = list(storage.outbox_path.glob("*.png"))
    assert len(survivors) == 1
    assert survivors[0].name.startswith("two--")


async def test_the_same_picture_is_written_once_however_often_it_appears(
    convert, client, storage, stub_engine, settings
):
    """A letterhead on every page of a two-thousand-page contract is one picture.

    Without this a real document produced roughly one file per page — 1,500 of them, of
    which a few dozen were distinct — and the outbox became unusable while the per-document
    ceiling cut the tail off the document (spec Edge Cases).
    """
    settings.extract_images = True
    letterhead = _distinct("letterhead")
    stub_engine.default_behavior = TaskBehavior(
        markdown=BODY, pictures=[letterhead, _distinct("figure"), letterhead, letterhead]
    )

    detail = await _detail(client, await convert(("repeated.pdf", pdf_bytes(b"rep"))))

    written = list(storage.outbox_path.glob("*.png"))
    assert len(written) == 2, "two distinct pictures, whatever the reference count"
    assert detail["image_count"] == 2

    referenced = re.findall(r"!\[\]\(([^)]+)\)", _outbox_markdown(storage))
    assert len(referenced) == 4, "every place the picture stood still points at it"
    assert len(set(referenced)) == 2
    for name in referenced:
        assert (storage.outbox_path / name).is_file()


async def test_the_ceiling_counts_distinct_pictures(convert, storage, stub_engine, settings):
    """The limit exists to bound the folder, and the folder holds distinct files."""
    settings.extract_images = True
    settings.image_max_per_document = 2
    repeated = _distinct("same")
    stub_engine.default_behavior = TaskBehavior(
        markdown=BODY, pictures=[repeated] * 5 + [_distinct("other")]
    )

    await convert(("ceiling.pdf", pdf_bytes(b"ceil")))

    assert len(list(storage.outbox_path.glob("*.png"))) == 2
