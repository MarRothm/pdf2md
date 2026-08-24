"""`POST /api/uploads` — accept one or more PDFs (FR-007, FR-008, FR-009)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile

from pdf2md.api import ApiError, db_of, settings_of, storage_of
from pdf2md.config import Settings
from pdf2md.dispatcher import claim_already_converted
from pdf2md.logging_config import log_job
from pdf2md.models import AcceptedUpload, JobStatus, RejectedUpload, UploadResponse
from pdf2md.pdfinfo import (
    EncryptedPdfError,
    UnreadablePdfError,
    page_count,
)
from pdf2md.storage import OutOfSpaceError, Storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["uploads"])

CHUNK_BYTES = 1 << 20
MAGIC_WINDOW = 1024
ENCRYPTION_TAIL_BYTES = 8192


class Rejection(Exception):
    """A refusal, with the sentence the operator reads and the one the library raised.

    `detail` exists because the two are not the same claim: "the file looks damaged" is a
    conclusion drawn from an exception, and when the conclusion is wrong there has to be
    something recorded to notice that by (FR-019).
    """

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@router.post("/uploads", status_code=202, response_model=UploadResponse)
async def create_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    note: str | None = Form(default=None),
) -> UploadResponse:
    settings = settings_of(request)
    db = db_of(request)
    storage = storage_of(request)

    if not files:
        raise ApiError(422, "no_files", "No files were submitted. Choose at least one PDF.")

    _require_space(storage, settings.min_free_bytes)

    batch_id = db.create_batch(document_count=0, submitter_note=note or None)
    accepted: list[AcceptedUpload] = []
    rejected: list[RejectedUpload] = []

    for upload in files:
        filename = _display_name(upload.filename)
        try:
            content_hash, size_bytes = await _store_upload(upload, filename, storage, settings)
        except Rejection as rejection:
            rejected.append(RejectedUpload(filename=filename, reason=rejection.reason))
            _log_rejection(filename, rejection)
            continue

        try:
            pages = _inspect_pages(storage.inbox_file(content_hash), filename, settings)
        except Rejection as rejection:
            rejected.append(RejectedUpload(filename=filename, reason=rejection.reason))
            _log_rejection(filename, rejection)
            continue

        db.upsert_source_document(
            content_hash=content_hash,
            original_filename=filename,
            size_bytes=size_bytes,
            page_count=pages,
            inbox_path=str(storage.inbox_file(content_hash)),
        )
        job = db.create_job(
            content_hash=content_hash, submitted_filename=filename, batch_id=batch_id
        )
        output_name = claim_already_converted(db, storage, job)
        status = JobStatus.ALREADY_CONVERTED if output_name else JobStatus.QUEUED
        accepted.append(
            AcceptedUpload(
                job_id=job.id, filename=filename, status=status, output_filename=output_name
            )
        )
        log_job(
            logger,
            "job_accepted",
            job_id=job.id,
            filename=filename,
            batch_id=batch_id,
            size_bytes=size_bytes,
            content_hash=content_hash[:12],
            status=status.value,
        )

    db.set_batch_document_count(batch_id, len(accepted))
    return UploadResponse(batch_id=batch_id, accepted=accepted, rejected=rejected)


async def _store_upload(
    upload: UploadFile, filename: str, storage: Storage, settings: Settings
) -> tuple[str, int]:
    """Stream one upload to the inbox, hashing as it goes; never held in memory whole."""
    handle, temp_path = storage.open_inbox_temp()
    digest = hashlib.sha256()
    size = 0
    head = b""
    tail = b""

    try:
        with os.fdopen(handle, "wb") as sink:
            while chunk := await upload.read(CHUNK_BYTES):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise Rejection(
                        f'"{filename}" is larger than the '
                        f"{_human(settings.max_upload_bytes)} limit for a single document."
                    )
                digest.update(chunk)
                if len(head) < MAGIC_WINDOW:
                    head += chunk[: MAGIC_WINDOW - len(head)]
                tail = (tail + chunk)[-ENCRYPTION_TAIL_BYTES:]
                sink.write(chunk)
        _validate(filename, head, tail, size)
    except Rejection:
        _discard(temp_path)
        raise
    except OSError as error:
        _discard(temp_path)
        if error.errno == 28:  # ENOSPC
            raise OutOfSpaceError("inbox") from error
        raise

    content_hash = digest.hexdigest()
    storage.commit_inbox_file(temp_path, content_hash)
    return content_hash, size


def _log_rejection(filename: str, rejection: Rejection) -> None:
    """The sentence the operator sees, and the one the library actually raised.

    Only the first was logged. "The file looks damaged" is a conclusion, and when it is
    wrong there was nothing else recorded to notice that by — the structural reader's own
    words never left the process (FR-019).
    """
    logger.info(
        'upload_rejected file="%s" reason="%s" detail="%s"',
        filename,
        rejection.reason,
        rejection.detail or "-",
    )


def _inspect_pages(path: Path, filename: str, settings: Settings) -> int:
    """Read the page count and decide the document's fate here, not forty minutes later.

    Every refusal below used to arrive as a conversion failure, and one of them arrived
    wearing the wrong reason entirely: a document over the engine's page ceiling was
    reported as probably damaged. Reading the page tree at upload is what lets each of
    these say the true thing immediately (FR-007, FR-036).
    """
    try:
        pages = page_count(path)
    except EncryptedPdfError as error:
        raise Rejection(
            f'"{filename}" is password-protected, so its contents cannot be read. '
            "Remove the password and upload it again."
        ) from error
    except UnreadablePdfError as error:
        raise Rejection(
            f'"{filename}" could not be read — the file looks damaged or incomplete. '
            "Try re-saving or re-exporting it, then upload it again.",
            detail=str(error),
        ) from error

    if pages > settings.max_total_pages:
        raise Rejection(
            f'"{filename}" has {pages:,} pages, more than the {settings.max_total_pages:,} '
            "this converter accepts in one document. Split it into smaller files and "
            "upload those."
        )
    return pages


def _validate(filename: str, head: bytes, tail: bytes, size: int) -> None:
    if size == 0:
        raise Rejection(f'"{filename}" is empty — there is nothing to convert.')
    if b"%PDF-" not in head:
        raise Rejection("Not a PDF. Only PDF files can be converted.")
    if b"/Encrypt" in tail:
        raise Rejection(
            f'"{filename}" is password-protected, so its contents cannot be read. '
            "Remove the password and upload it again."
        )


def _require_space(storage: Storage, min_free_bytes: int) -> None:
    for path, location in ((storage.inbox_path, "inbox"), (storage.outbox_path, "outbox")):
        free = storage.free_bytes(path)
        if free is not None and free < min_free_bytes:
            raise OutOfSpaceError(location)


def _discard(temp_path: Path) -> None:
    temp_path.unlink(missing_ok=True)


def _display_name(raw: str | None) -> str:
    """Filenames are for display only — the content hash is the identity."""
    name = (raw or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    return name.strip() or "document.pdf"


def _human(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.0f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} bytes"
