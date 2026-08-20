"""`GET /api/jobs`, job detail, the Markdown download, and retry."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request, Response

from pdf2md.api import ApiError, db_of, storage_of
from pdf2md.clock import now_iso, parse_iso
from pdf2md.db import Database, JobView
from pdf2md.deletion import DeletionRefused, UnknownJob, delete_document, delete_everything
from pdf2md.logging_config import log_job
from pdf2md.models import (
    DOWNLOADABLE_STATUSES,
    TERMINAL_STATUSES,
    BulkDeletionResult,
    DeletionResult,
    JobDetail,
    JobListResponse,
    JobStatus,
    JobSummary,
    MissingPart,
    OutputFile,
    PartStatus,
    RetryResponse,
    display_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

MAX_LIMIT = 500


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=MAX_LIMIT),
    batch_id: str | None = None,
    status: list[str] | None = Query(default=None),
    since: str | None = None,
    content_hash: str | None = None,
) -> JobListResponse:
    db = db_of(request)
    if since:
        _validate_since(since)
    for value in status or []:
        _validate_status(value)
    views = db.job_views(
        limit=limit,
        batch_id=batch_id,
        statuses=status,
        since=since,
        content_hash=content_hash,
    )
    return JobListResponse(
        server_time=now_iso(),
        backlog=db.backlog(),
        jobs=[to_summary(view) for view in views],
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(request: Request, job_id: str) -> JobDetail:
    db = db_of(request)
    storage = storage_of(request)
    view = _require_view(db, job_id)
    recorded = db.outputs_for_hash(view.job.content_hash)
    # What the operator will actually find in the outbox — one file, or one per section
    # for a document above the threshold (FR-033).
    outputs = [_output_file(output) for output in recorded if output.job_id == view.job.id]
    # Every file for the document, whichever job wrote it. The delete confirmation is
    # built from this: an `already_converted` job's own `outputs` is empty while the
    # document it points at has files (feature 002, FR-017).
    document_outputs = [_output_file(output) for output in recorded]
    return to_detail(
        view,
        outputs,
        document_outputs=document_outputs,
        retained_upload=storage.has_inbox_file(view.job.content_hash),
        missing_parts=_missing_parts(db, view),
    )


@router.get("/{job_id}/markdown")
async def download_markdown(request: Request, job_id: str) -> Response:
    db = db_of(request)
    storage = storage_of(request)
    view = _require_view(db, job_id)
    job = view.job

    if job.status not in TERMINAL_STATUSES:
        raise ApiError(
            409,
            "still_converting",
            f'"{job.submitted_filename}" is still converting. '
            "The Markdown will be available when it finishes.",
        )
    if job.status not in DOWNLOADABLE_STATUSES or not job.output_filename:
        raise ApiError(
            404,
            "no_output",
            f'No Markdown was produced for "{job.submitted_filename}" — '
            "the conversion did not succeed.",
        )

    path = storage.outbox_file(job.output_filename)
    if not path.is_file():
        raise ApiError(
            404,
            "output_removed",
            f'"{job.output_filename}" is no longer in the output folder. '
            "It was produced, but has since been removed from the server.",
        )

    return Response(
        content=path.read_bytes(),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job.output_filename}"'},
    )


@router.delete("", response_model=BulkDeletionResult)
async def delete_all_jobs(request: Request) -> BulkDeletionResult:
    """Remove every document, every entry, every output file, and every retained upload.

    The clean slate (FR-027). Irreversible, and it takes successful conversions with it —
    the Markdown in the outbox belongs to the documents being deleted. A document the
    engine is converting is skipped and named in the response rather than blocking the
    whole clear.
    """
    return delete_everything(db_of(request), storage_of(request))


@router.delete("/{job_id}", response_model=DeletionResult)
async def delete_job(request: Request, job_id: str) -> DeletionResult:
    """Delete the document this conversion belongs to — every entry, file, and the upload.

    Irreversible, and deliberately without a `?confirm=` flag: a flag a client can set is
    not a confirmation a person gave. The page asks (FR-014); this performs.
    """
    db = db_of(request)
    storage = storage_of(request)
    try:
        result = delete_document(db, storage, job_id)
    except UnknownJob as error:
        raise ApiError(
            404,
            "already_deleted",
            "That document is no longer in the list — it has already been deleted.",
        ) from error
    except DeletionRefused as error:
        raise ApiError(409, "still_converting", error.message) from error
    return result


@router.post("/{job_id}/retry", status_code=202, response_model=RetryResponse)
async def retry_job(request: Request, job_id: str) -> RetryResponse:
    db = db_of(request)
    storage = storage_of(request)
    view = _require_view(db, job_id)
    job = view.job

    if job.status not in TERMINAL_STATUSES:
        raise ApiError(
            409,
            "still_converting",
            f'"{job.submitted_filename}" is still converting — there is nothing to retry yet.',
        )
    if job.status in DOWNLOADABLE_STATUSES:
        raise ApiError(
            409,
            "already_converted",
            f'"{job.submitted_filename}" already converted, so there is nothing to retry.',
        )
    if not storage.has_inbox_file(job.content_hash):
        raise ApiError(
            409,
            "upload_gone",
            f'The uploaded copy of "{job.submitted_filename}" is no longer on the server. '
            "Please upload the document again.",
        )

    retry = db.create_job(
        content_hash=job.content_hash,
        submitted_filename=job.submitted_filename,
        batch_id=job.batch_id,
    )
    log_job(
        logger,
        "job_retry_requested",
        job_id=retry.id,
        filename=retry.submitted_filename,
        original_job_id=job.id,
    )
    return RetryResponse(job_id=retry.id, status=retry.status)


# --- shaping --------------------------------------------------------------


def _output_file(output) -> OutputFile:
    return OutputFile(
        filename=output.output_filename,
        bytes=output.bytes,
        section_title=output.section_title,
    )


def to_summary(view: JobView) -> JobSummary:
    job = view.job
    downloadable = job.status in DOWNLOADABLE_STATUSES and bool(job.output_filename)
    return JobSummary(
        job_id=job.id,
        batch_id=job.batch_id,
        filename=job.submitted_filename,
        content_hash=job.content_hash,
        status=job.status,
        display_status=display_status(
            job.status,
            part_count=job.part_count,
            parts_completed=job.parts_completed,
            missing_page_ranges=job.missing_page_ranges,
        ),
        queue_position=job.queue_position,
        created_at=job.created_at,
        started_at=job.started_at,
        ended_at=job.ended_at,
        attempt=job.attempt,
        size_bytes=view.size_bytes,
        page_count=view.page_count,
        failure_reason=job.failure_reason,
        output_filename=job.output_filename,
        download_url=f"/api/jobs/{job.id}/markdown" if downloadable else None,
        engine_status=view.engine_status,
        part_count=job.part_count,
        parts_completed=job.parts_completed,
        missing_page_ranges=job.missing_page_ranges,
    )


def to_detail(
    view: JobView,
    outputs: list[OutputFile] | None = None,
    *,
    document_outputs: list[OutputFile] | None = None,
    retained_upload: bool = False,
    missing_parts: list[MissingPart] | None = None,
) -> JobDetail:
    job = view.job
    return JobDetail(
        **to_summary(view).model_dump(),
        engine_errors=job.engine_errors,
        processing_seconds=_processing_seconds(view),
        output_bytes=view.output_bytes,
        outputs=outputs or [],
        document_outputs=document_outputs or [],
        retained_upload=retained_upload,
        missing_parts=missing_parts or [],
    )


def _missing_parts(db: Database, view: JobView) -> list[MissingPart]:
    """Why each gap in a finished document is a gap (FR-038).

    Only the ranges that are absent: the ones that converted are in the file, and a
    fifty-part document would otherwise answer a question nobody asked with fifty rows.
    """
    if view.job.part_count < 2 or not view.job.missing_page_ranges:
        return []
    return [
        MissingPart(
            first_page=part.first_page,
            last_page=part.last_page,
            status=part.status,
            attempts=part.attempt,
            failure_reason=part.failure_reason,
        )
        for part in db.parts_for_job(view.job.id)
        if part.status is not PartStatus.SUCCEEDED
    ]


def _processing_seconds(view: JobView) -> float | None:
    job = view.job
    if not job.started_at or not job.ended_at:
        return None
    return round((parse_iso(job.ended_at) - parse_iso(job.started_at)).total_seconds(), 1)


def _require_view(db: Database, job_id: str) -> JobView:
    view = db.job_view(job_id)
    if view is None:
        raise ApiError(
            404,
            "job_not_found",
            "That document is not in the job list. Older jobs are removed from the "
            "history, but their Markdown stays in the output folder.",
        )
    return view


def _validate_status(value: str) -> None:
    try:
        JobStatus(value)
    except ValueError as error:
        raise ApiError(422, "unknown_status", f'"{value}" is not a job status.') from error


def _validate_since(value: str) -> None:
    try:
        parse_iso(value)
    except ValueError as error:
        raise ApiError(422, "bad_since", '"since" must be an ISO-8601 timestamp.') from error
