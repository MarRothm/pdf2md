"""Domain entities (data-model.md) and API payload shapes (contracts/web-api.md)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SUCCEEDED_SUSPECT = "succeeded_suspect"
    SUCCEEDED_INCOMPLETE = "succeeded_incomplete"
    ALREADY_CONVERTED = "already_converted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.SUCCEEDED_SUSPECT,
        JobStatus.SUCCEEDED_INCOMPLETE,
        JobStatus.ALREADY_CONVERTED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
    }
)

IN_FLIGHT_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.QUEUED, JobStatus.SUBMITTED, JobStatus.RUNNING}
)

CONVERTING_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.SUBMITTED, JobStatus.RUNNING})

DOWNLOADABLE_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.SUCCEEDED_SUSPECT,
        JobStatus.SUCCEEDED_INCOMPLETE,
        JobStatus.ALREADY_CONVERTED,
    }
)

DISPLAY_STATUS: dict[JobStatus, str] = {
    JobStatus.QUEUED: "Queued",
    JobStatus.SUBMITTED: "Converting",
    JobStatus.RUNNING: "Converting",
    JobStatus.SUCCEEDED: "Converted",
    JobStatus.SUCCEEDED_SUSPECT: "Converted — check output",
    JobStatus.SUCCEEDED_INCOMPLETE: "Converted — some pages are missing",
    JobStatus.ALREADY_CONVERTED: "Already converted",
    JobStatus.FAILED: "Failed",
    JobStatus.TIMED_OUT: "Timed out",
}


def display_status(
    status: JobStatus | str,
    *,
    part_count: int = 1,
    parts_completed: int = 0,
    missing_page_ranges: list[list[int]] | None = None,
) -> str:
    """The user-facing string for a job state, so the page never maps states itself.

    A split document says which part is running: an hour with no visible movement is
    indistinguishable from a stall, and the part counter is what makes legitimate slow
    progress legible (FR-037).
    """
    state = JobStatus(status)
    if state in CONVERTING_STATUSES and part_count > 1:
        return f"Converting — part {min(parts_completed + 1, part_count)} of {part_count}"
    if state is JobStatus.QUEUED and part_count > 1 and parts_completed:
        # A document resumed after a restart is queued with most of its work already done,
        # and a bare "Queued" reads as *back to the beginning* on a job that took hours.
        # The parts that converted kept their Markdown; say so (FR-037).
        return f"Queued — {parts_completed} of {part_count} parts already converted"
    if state is JobStatus.SUCCEEDED_INCOMPLETE and missing_page_ranges:
        ranges = ", ".join(
            f"{first}" if first == last else f"{first}-{last}"
            for first, last in missing_page_ranges
        )
        return f"Converted — pages {ranges} are missing"
    return DISPLAY_STATUS[state]


class EngineStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    SKIPPED = "skipped"
    FAILURE = "failure"


# --- entities -------------------------------------------------------------


class Batch(BaseModel):
    id: str
    created_at: str
    document_count: int
    submitter_note: str | None = None


class SourceDocument(BaseModel):
    content_hash: str
    original_filename: str
    size_bytes: int
    page_count: int | None = None
    first_seen_at: str
    inbox_path: str | None = None


class ConversionJob(BaseModel):
    id: str
    batch_id: str | None = None
    content_hash: str
    submitted_filename: str
    status: JobStatus
    engine_task_id: str | None = None
    queue_position: int | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    ended_at: str | None = None
    attempt: int = 1
    failure_reason: str | None = None
    engine_errors: list[str] | None = None
    output_filename: str | None = None
    part_count: int = 1
    parts_completed: int = 0
    missing_page_ranges: list[list[int]] | None = None


class PartStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


TERMINAL_PART_STATUSES: frozenset[PartStatus] = frozenset(
    {PartStatus.SUCCEEDED, PartStatus.FAILED, PartStatus.TIMED_OUT}
)


class ConversionPart(BaseModel):
    """One conversion of one page range — bookkeeping beneath a job (data-model.md).

    The page never shows these; it shows their count. `markdown` is held here rather than
    in a scratch file because the engine serves each result exactly once, so a part's
    output has to become durable in the same step that fetches it (research.md R3).
    """

    id: str
    job_id: str
    ordinal: int
    first_page: int
    last_page: int
    part_path: str | None = None
    status: PartStatus
    engine_task_id: str | None = None
    markdown: str | None = None
    failure_reason: str | None = None
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    attempt: int = 1
    split_depth: int = 0
    """How often this range has already been halved after running out of time (FR-038)."""

    image_plan: str | None = None
    """JSON: what this part's pictures are and where its scratch files sit, until the join
    can give them document-wide ordinals (feature 003, research R6)."""


class ExtractedImage(BaseModel):
    """One picture written to the outbox and referenced from the Markdown (feature 003).

    The row is what makes the file findable, replaceable, and deletable: nothing in this
    service removes an outbox file it cannot name from the database, and nothing scans the
    folder (feature 002 INV-2).
    """

    image_filename: str
    content_hash: str
    job_id: str
    ordinal: int
    page_no: int | None = None
    mimetype: str
    bytes: int
    written_at: str


class MarkdownOutput(BaseModel):
    output_filename: str
    content_hash: str
    job_id: str
    bytes: int
    written_at: str
    engine_status: str
    section_ordinal: int | None = None
    """A document above the section threshold writes one row per section (FR-033)."""
    section_title: str | None = None


# --- API payloads ---------------------------------------------------------


class AcceptedUpload(BaseModel):
    job_id: str
    filename: str
    status: JobStatus
    output_filename: str | None = None


class RejectedUpload(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    batch_id: str
    accepted: list[AcceptedUpload]
    rejected: list[RejectedUpload]


class Backlog(BaseModel):
    queued: int = 0
    converting: int = 0


class JobSummary(BaseModel):
    job_id: str
    batch_id: str | None
    filename: str
    content_hash: str
    """Two rows sharing this are two conversions of one PDF; they are deleted together."""
    status: JobStatus
    display_status: str
    queue_position: int | None
    created_at: str
    started_at: str | None
    ended_at: str | None
    attempt: int
    size_bytes: int
    page_count: int | None
    failure_reason: str | None
    output_filename: str | None
    """The first file the document produced. For a sectioned document that is section
    one of many, which is why `download_all_url` exists (FR-043)."""

    output_file_count: int = 1
    image_count: int = 0
    """Pictures extracted for this document. Distinguishes a document with no pictures
    from one whose pictures were not extracted (feature 003, FR-013)."""
    download_url: str | None
    download_all_url: str | None = None
    """Every file the document produced, as one archive. Set only when there is more
    than one — otherwise the single-file download already is the document."""
    engine_status: str | None = None
    """`success` or `partial_success`; the page shows the latter distinctly."""
    part_count: int = 1
    """1 for a document converted whole, so the page needs no special case (FR-037)."""
    parts_completed: int = 0
    missing_page_ranges: list[list[int]] | None = None
    """Set only for `succeeded_incomplete`: the ranges whose part failed (FR-035)."""


class MissingPart(BaseModel):
    """One page range that is absent from a finished document, and why (FR-038).

    Without this the page can say only *pages 1-100 are missing*, which is the same
    sentence whether the engine ran out of time, forgot the task, or found the pages
    unreadable — three problems with three different answers.
    """

    first_page: int
    last_page: int
    status: PartStatus
    attempts: int
    failure_reason: str | None = None


class OutputFile(BaseModel):
    filename: str
    bytes: int
    section_title: str | None = None


class JobDetail(JobSummary):
    engine_errors: list[str] | None = None
    processing_seconds: float | None = None
    output_bytes: int | None = None
    outputs: list[OutputFile] = []
    """The files *this job* wrote — one, or one per section (FR-033)."""
    document_outputs: list[OutputFile] = []
    """Every file recorded for the document, whichever job wrote it.

    Differs from `outputs` for an `already_converted` job, whose output rows carry the
    original converter's id. A delete confirmation built from `outputs` would promise to
    remove nothing while removing every section file (feature 002, FR-017).
    """
    retained_upload: bool = False
    """Whether the uploaded PDF is still on the server and would be discarded with it."""

    missing_parts: list[MissingPart] = []
    """The ranges behind `missing_page_ranges`, each with the reason it is missing."""


class JobListResponse(BaseModel):
    server_time: str
    backlog: Backlog
    jobs: list[JobSummary]


class DeletionResult(BaseModel):
    """What `DELETE /api/jobs/{job_id}` actually removed (feature 002)."""

    job_ids: list[str]
    """Every list entry removed, not only the one addressed."""
    filename: str
    removed_files: list[str]
    kept_files: list[str]
    """Files that could not be unlinked. Empty on a clean deletion (FR-018)."""
    upload_discarded: bool


class SkippedDocument(BaseModel):
    filename: str
    reason: str


class BulkDeletionResult(BaseModel):
    """What `DELETE /api/jobs` removed. Irreversible (feature 002, FR-027)."""

    documents_deleted: int
    job_ids: list[str]
    removed_files: list[str]
    kept_files: list[str]
    skipped: list[SkippedDocument] = []
    """Documents the engine was converting. Nothing of theirs was touched."""


class RetryResponse(BaseModel):
    job_id: str
    status: JobStatus


class EngineHealth(BaseModel):
    reachable: bool
    checked_at: str


class OutboxHealth(BaseModel):
    writable: bool
    free_bytes: int | None = None
    documents: int = 0


class DatabaseHealth(BaseModel):
    writable: bool


class DispatcherHealth(BaseModel):
    """Whether work is actually moving, and why not when it is not.

    Engine reachability is not the same question: an engine that answers `/ready` can
    still refuse every submission, and the conversion loop can stop while everything it
    depends on stays healthy. Both present as a queue that never empties under a status
    line saying the converter is ready.
    """

    running: bool = True
    last_pass_at: str | None = None
    last_engine_error: str | None = None
    last_engine_error_at: str | None = None
    engine_restarts_recent: int = 0
    """Tasks the engine has forgotten lately. Each one is a restart under live work, and
    the parts that were with it fail saying their result was lost — which reads as a
    document problem and is not one."""


class HealthResponse(BaseModel):
    status: str
    engine: EngineHealth
    backlog: Backlog
    outbox: OutboxHealth
    database: DatabaseHealth
    version: str
    dispatcher: DispatcherHealth = DispatcherHealth()


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody = Field(...)
