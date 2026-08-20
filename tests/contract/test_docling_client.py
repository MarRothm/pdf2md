"""The engine client against contracts/docling-serve.md."""

import httpx
import pytest

from pdf2md.docling_client import DoclingClient, EngineUnavailableError, TaskStatus
from tests.conftest import pdf_bytes
from tests.stubs.docling_stub import TaskBehavior

pytestmark = pytest.mark.contract


@pytest.fixture
def engine(stub_engine):
    return DoclingClient(
        base_url="http://engine.test",
        api_key="test-key",
        transport=httpx.ASGITransport(app=stub_engine.app),
    )


async def test_submission_sends_the_documented_multipart_fields(engine, stub_engine):
    async with engine:
        await engine.submit("report.pdf", pdf_bytes(b"a"))
    (submission,) = stub_engine.submissions
    assert submission["filename"] == "report.pdf"
    assert submission["from_formats"] == ["pdf"]
    assert submission["to_formats"] == ["md"]
    assert submission["do_ocr"] == "true"


async def test_submission_carries_the_api_key(engine, stub_engine):
    stub_engine.api_key = "other-key"
    async with engine:
        with pytest.raises(EngineUnavailableError):
            await engine.submit("report.pdf", pdf_bytes(b"a"))


async def test_submission_returns_the_task_id_and_position(engine):
    async with engine:
        submitted = await engine.submit("report.pdf", pdf_bytes(b"a"))
    assert submitted.task_id
    assert submitted.task_status is TaskStatus.PENDING
    assert submitted.task_position == 0


async def test_poll_reports_pending_then_started_then_success(engine, stub_engine):
    stub_engine.default_behavior = TaskBehavior(polls_pending=1, polls_running=1)
    async with engine:
        submitted = await engine.submit("report.pdf", pdf_bytes(b"a"))
        statuses = [(await engine.poll(submitted.task_id)).task_status for _ in range(3)]
    assert statuses == [TaskStatus.PENDING, TaskStatus.STARTED, TaskStatus.SUCCESS]


async def test_result_is_parsed_into_markdown_status_and_errors(engine, stub_engine):
    stub_engine.default_behavior = TaskBehavior(
        markdown="# Title\n\ntext",
        result_status="partial_success",
        errors=["Page 14: table structure could not be resolved"],
        page_count=14,
        processing_time=96.4,
        polls_pending=0,
        polls_running=0,
    )
    async with engine:
        submitted = await engine.submit("report.pdf", pdf_bytes(b"a"))
        result = await engine.fetch_result(submitted.task_id)
    assert result.markdown == "# Title\n\ntext"
    assert result.status == "partial_success"
    assert result.errors == ["Page 14: table structure could not be resolved"]
    assert result.page_count == 14
    assert result.processing_time == 96.4


async def test_a_result_is_fetched_at_most_once_per_job(engine, stub_engine):
    async with engine:
        submitted = await engine.submit("report.pdf", pdf_bytes(b"a"))
        await engine.fetch_result(submitted.task_id)
        with pytest.raises(EngineUnavailableError):
            await engine.fetch_result(submitted.task_id)
    assert stub_engine.result_fetches.count(submitted.task_id) == 2


async def test_health_reflects_engine_readiness(engine, stub_engine):
    async with engine:
        assert await engine.is_healthy() is True
        stub_engine.models_ready = False  # warming up: reachable, but cannot take work
        assert await engine.is_healthy() is False
        stub_engine.models_ready = True
        stub_engine.reachable = False
        assert await engine.is_healthy() is False


async def test_engine_errors_become_plain_language_failure_reasons():
    reason = DoclingClient.failure_reason_from(
        engine_status="failure",
        errors=["Traceback (most recent call last): RuntimeError: PdfiumError code 4"],
    )
    assert "Traceback" not in reason
    assert "RuntimeError" not in reason
    assert reason[0].isupper() and reason.endswith(".")


async def test_the_recognition_language_is_sent_when_it_is_configured(stub_engine):
    """German scans come back without their umlauts under the engine's own default, whose
    bundled weights are English and Chinese (research.md R4, FR-039)."""
    client = DoclingClient(
        base_url="http://engine.test",
        api_key="test-key",
        transport=httpx.ASGITransport(app=stub_engine.app),
        ocr_preset="easyocr",
        ocr_languages=["de", "en"],
    )
    async with client:
        await client.submit("scan.pdf", pdf_bytes(b"scan"))

    (submission,) = stub_engine.submissions
    assert submission["ocr_preset"] == "easyocr"
    assert submission["ocr_lang"] == ["de", "en"]


async def test_an_unconfigured_deployment_submits_what_it_always_did(engine, stub_engine):
    """Neither field is sent unless it is set, so the engine's own defaults still apply."""
    async with engine:
        await engine.submit("plain.pdf", pdf_bytes(b"plain"))

    (submission,) = stub_engine.submissions
    assert submission["ocr_preset"] == "auto"
    assert submission["ocr_lang"] == []
