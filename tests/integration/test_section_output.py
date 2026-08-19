"""Section files for a large document, and what happens when it is converted again.

FR-033: above a size threshold the outbox receives one file per section, so an answer
drawn from a long document cites the section it came from. research.md R13: re-converting
replaces that document's own section files, because an engine upgrade can detect different
headings and would otherwise leave two contradictory versions for AnythingLLM to cite.
"""

import pytest

from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.integration


def _markdown(*titles: str, body: str = "x" * 30_000) -> str:
    return "".join(f"# {title}\n\n{body}\n\n" for title in titles)


async def test_a_large_document_is_written_as_section_files(
    convert, client, storage, stub_engine, settings
):
    settings.section_split_threshold_bytes = 1000
    settings.section_min_bytes = 100
    settings.section_max_bytes = 10**6
    stub_engine.default_behavior = TaskBehavior(markdown=_markdown("Alpha", "Beta", "Gamma"))

    response = await convert(("manual.pdf", pdf_bytes(b"m")))
    names = sorted(path.name for path in storage.outbox_path.glob("*.md"))

    assert len(names) == 3
    assert names[0].endswith("--001-alpha.md")
    assert names[2].endswith("--003-gamma.md")

    detail = (await client.get(f"/api/jobs/{response.json()['accepted'][0]['job_id']}")).json()
    assert [output["section_title"] for output in detail["outputs"]] == ["Alpha", "Beta", "Gamma"]


async def test_a_small_document_still_gets_exactly_one_file(
    convert, storage, stub_engine, settings
):
    """The common case is untouched — this feature is for the documents that need it."""
    settings.section_split_threshold_bytes = 10**6
    stub_engine.default_behavior = TaskBehavior(markdown=_markdown("Alpha", "Beta"))

    await convert(("small.pdf", pdf_bytes(b"s")))
    assert len(list(storage.outbox_path.glob("*.md"))) == 1


async def test_reconverting_replaces_the_previous_section_files(
    convert, storage, stub_engine, settings, db, dispatcher
):
    """The one place this service deletes from the outbox, and only its own files."""
    settings.section_split_threshold_bytes = 1000
    settings.section_min_bytes = 100
    settings.section_max_bytes = 10**6
    stub_engine.default_behavior = TaskBehavior(markdown=_markdown("Alpha", "Beta", "Gamma"))

    await convert(("manual.pdf", pdf_bytes(b"m")))
    assert len(list(storage.outbox_path.glob("*.md"))) == 3

    # Another operator's document, which must survive untouched.
    stub_engine.set_behavior("other.pdf", TaskBehavior(markdown="unrelated " * 100))
    await convert(("other.pdf", pdf_bytes(b"other")))
    before = {path.name for path in storage.outbox_path.glob("*.md")}

    # The same document again, with the engine now detecting two headings instead of three.
    job = db.create_job(
        content_hash=next(iter(db.documents_with_inbox_file())).content_hash,
        submitted_filename="manual.pdf",
        batch_id=None,
    )
    dispatcher.persist_markdown(job, _markdown("Alpha", "Beta"), engine_status="success")

    after = {path.name for path in storage.outbox_path.glob("*.md")}
    assert not any(name.endswith("--003-gamma.md") for name in after)
    assert len([name for name in after if "manual" in name]) == 2
    assert before - after == {name for name in before if name.endswith("--003-gamma.md")}
