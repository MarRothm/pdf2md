"""Client for the upstream engine's async task API (contracts/docling-serve.md).

Three calls make up a conversion: submit, poll, fetch. The fetch is the delicate
one — `DOCLING_SERVE_SINGLE_USE_RESULTS` means the engine serves each result
exactly once, so this client never retries it (research.md R3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType

import httpx

from pdf2md.models import EngineStatus, JobStatus

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"


class EngineUnavailableError(RuntimeError):
    """The engine could not be reached, or answered in a way we cannot use."""


class TaskNotFoundError(EngineUnavailableError):
    """The engine has no record of this task — it restarted, or the result expired."""


@dataclass
class SubmittedTask:
    task_id: str
    task_status: TaskStatus
    task_position: int | None = None


@dataclass
class PollResult:
    task_id: str
    task_status: TaskStatus
    task_position: int | None = None


@dataclass
class ConversionResult:
    markdown: str
    status: str
    errors: list[str] = field(default_factory=list)
    processing_time: float | None = None
    page_count: int | None = None


# Failure text is written for the person who uploaded the document, not for us
# (FR-011). Engine detail is kept in `engine_errors` and the container log.
_FAILURE_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (
        ("password", "encrypt", "decrypt"),
        "This PDF is password-protected, so its contents could not be read. "
        "Remove the password and upload it again.",
    ),
    (
        ("timeout", "timed out", "deadline"),
        "This document took too long to convert and was stopped. "
        "It may be very large or unusually complex.",
    ),
    (
        ("too many pages", "max_num_pages", "file size", "too large"),
        "This document is larger than the converter accepts. "
        "Split it into smaller files and try again.",
    ),
    (
        # docling's InputDocument marks a file invalid for seven different reasons —
        # size, page count, backend load failure, and others — and none of them mean the
        # file is damaged. Saying so would send someone to re-export a perfectly good PDF.
        ("not valid", "not allowed", "policy"),
        "The converter would not accept this document. It may be protected, or saved in a "
        "form the converter cannot open — try re-exporting it as a PDF from the original "
        "application.",
    ),
    (
        ("pdfium", "corrupt", "damaged", "malformed", "eof", "parse", "invalid pdf", "cannot read"),
        "This PDF could not be read — the file looks damaged or incomplete. "
        "Try re-saving or re-exporting it, then upload it again.",
    ),
    (
        ("memory", "oom", "resource"),
        "The converter ran out of resources on this document. "
        "Try again later, or split the document into smaller files.",
    ),
    (
        ("unsupported", "skipped", "not supported"),
        "The converter could not handle this document's contents. If it is a scan, try "
        "re-exporting it as a PDF from the original application.",
    ),
]

_GENERIC_FAILURE = (
    "This document could not be converted. Upload it again; if it fails a second time "
    "the PDF is probably damaged."
)


class DoclingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        health_path: str = "/ready",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.health_path = health_path
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            headers={"X-Api-Key": api_key},
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
        )

    async def __aenter__(self) -> DoclingClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- calls ------------------------------------------------------------

    async def submit(self, filename: str, payload: bytes) -> SubmittedTask:
        """`POST /v1/convert/file/async`, with every option sent explicitly."""
        files = {"files": (filename, payload, "application/pdf")}
        data = {"from_formats": ["pdf"], "to_formats": ["md"], "do_ocr": "true"}
        body = await self._request("POST", "/v1/convert/file/async", files=files, data=data)
        return SubmittedTask(
            task_id=str(body["task_id"]),
            task_status=_task_status(body.get("task_status")),
            task_position=body.get("task_position"),
        )

    async def poll(self, task_id: str) -> PollResult:
        body = await self._request("GET", f"/v1/status/poll/{task_id}")
        return PollResult(
            task_id=task_id,
            task_status=_task_status(body.get("task_status")),
            task_position=body.get("task_position"),
        )

    async def fetch_result(self, task_id: str) -> ConversionResult:
        """Called exactly once per job — the engine will not serve it again."""
        body = await self._request("GET", f"/v1/result/{task_id}")
        document = body.get("document") or {}
        return ConversionResult(
            markdown=document.get("md_content") or "",
            status=str(body.get("status") or EngineStatus.FAILURE),
            errors=[str(error) for error in (body.get("errors") or [])],
            processing_time=body.get("processing_time"),
            page_count=_page_count(body, document),
        )

    async def is_healthy(self) -> bool:
        """`/ready` answers 200 only once the models are loaded and the queue is live.

        Verified against the pinned engine tag: `/health` reports merely that the
        process is up, and neither endpoint requires the API key.
        """
        try:
            response = await self._client.get(self.health_path)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def _request(self, method: str, path: str, **kwargs: object) -> dict:
        try:
            response = await self._client.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as error:
            raise EngineUnavailableError(f"{method} {path}: {error}") from error
        if response.status_code == 404:
            raise TaskNotFoundError(f"{method} {path}: the engine has no record of this task")
        if response.status_code >= 400:
            raise EngineUnavailableError(f"{method} {path}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise EngineUnavailableError(f"{method} {path}: response was not JSON") from error

    # --- mapping onto our job states --------------------------------------

    @staticmethod
    def job_status_for(task_status: TaskStatus, engine_status: str | None = None) -> JobStatus:
        """The mapping table in contracts/docling-serve.md."""
        if task_status is TaskStatus.PENDING:
            return JobStatus.SUBMITTED
        if task_status is TaskStatus.STARTED:
            return JobStatus.RUNNING
        if task_status is TaskStatus.FAILURE:
            return JobStatus.FAILED
        if engine_status in (EngineStatus.SUCCESS, EngineStatus.PARTIAL_SUCCESS):
            return JobStatus.SUCCEEDED
        return JobStatus.FAILED

    @staticmethod
    def failure_reason_from(
        *, engine_status: str | None = None, errors: list[str] | None = None
    ) -> str:
        """Turn engine detail into one sentence a non-technical reader can act on."""
        haystack = " ".join(errors or []).lower()
        if engine_status:
            haystack = f"{haystack} {engine_status.lower()}"
        for keywords, message in _FAILURE_PATTERNS:
            if any(keyword in haystack for keyword in keywords):
                return message
        return _GENERIC_FAILURE


def _task_status(value: object) -> TaskStatus:
    try:
        return TaskStatus(str(value))
    except ValueError as error:
        raise EngineUnavailableError(f"unknown task_status {value!r}") from error


def _page_count(body: dict, document: dict) -> int | None:
    for source in (document, body):
        for key in ("page_count", "num_pages", "pages"):
            value = source.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return None
