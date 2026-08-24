"""Deleting a document: what goes, in what order, and what is refused.

The operator clicks a row, which is a job, but the unit of deletion is the *source
document* it belongs to. Conversions of one PDF share its Markdown, its retained upload,
and the record that makes a re-upload count as already converted, so deleting one job and
leaving its siblings would leave those siblings pointing at files that no longer exist
(FR-021).

The order is load-bearing: files first, then rows. A crash between the two leaves records
pointing at absent files, which every read path already survives — the download answers
`output_removed`, and `claim_already_converted` refuses to short-circuit a re-upload when
the file is gone. The reverse order would leave `.md` files in the outbox that no record
mentions, invisible to the page and undeletable through it (research.md R7).
"""

from __future__ import annotations

import logging

from pdf2md.db import Database
from pdf2md.models import (
    CONVERTING_STATUSES,
    BulkDeletionResult,
    ConversionJob,
    DeletionResult,
    SkippedDocument,
)
from pdf2md.storage import Storage

logger = logging.getLogger(__name__)


class DeletionRefused(Exception):
    """A conversion of this document is still in flight; nothing was touched."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownJob(Exception):
    """No such job. Already deleted, or pruned from the history."""


def delete_document(db: Database, storage: Storage, job_id: str) -> DeletionResult:
    """Remove the document this job belongs to, and everything it produced."""
    job = db.get_job(job_id)
    if job is None:
        raise UnknownJob(job_id)

    siblings = db.jobs_for_hash(job.content_hash)
    _refuse_if_converting(job, siblings)

    result = _delete_one(db, storage, job.content_hash, job.submitted_filename)

    logger.info(
        'document_deleted file="%s" content_hash=%s jobs=%d removed=%d kept=%s upload=%s',
        job.submitted_filename,
        job.content_hash[:12],
        len(result.job_ids),
        len(result.removed_files),
        ",".join(result.kept_files) or "-",
        "discarded" if result.upload_discarded else "already gone",
    )
    return result


def _delete_one(db: Database, storage: Storage, content_hash: str, filename: str) -> DeletionResult:
    """Files first, then rows, for one document. The order is the durability rule (R7)."""
    # Only names this service recorded as its own output. Never a directory scan, never a
    # path from the request (INV-2).
    output_filenames = [output.output_filename for output in db.outputs_for_hash(content_hash)]
    # A picture is part of what the conversion produced (feature 003 FR-008). Left behind,
    # it is exactly the orphan in the outbox that this feature exists to prevent — and it
    # is only removable because a row records its name; nothing here ever scans the folder.
    output_filenames += [image.image_filename for image in db.images_for_hash(content_hash)]

    removed, kept = storage.delete_outbox_files(output_filenames)

    upload_discarded = storage.has_inbox_file(content_hash)
    storage.delete_inbox_file(content_hash)
    storage.delete_part_files(content_hash)

    job_ids = db.delete_document_rows(content_hash)

    for name in kept:
        logger.warning('output_not_removed file="%s" content_hash=%s', name, content_hash[:12])

    return DeletionResult(
        job_ids=job_ids,
        filename=filename,
        removed_files=removed,
        kept_files=kept,
        upload_discarded=upload_discarded,
    )


def delete_everything(db: Database, storage: Storage) -> BulkDeletionResult:
    """Remove every document, every entry, every output file, and every retained upload.

    The clean slate an operator asks for when the list has filled with failed and abandoned
    attempts. Irreversible, and it takes successful conversions with it: the Markdown in the
    outbox belongs to the documents being deleted (FR-027).

    A document the engine is converting is skipped rather than deleted, and named in the
    result — the same rule as a single deletion, applied per document so that one busy
    conversion does not block clearing everything else.
    """
    deleted = 0
    job_ids: list[str] = []
    removed: list[str] = []
    kept: list[str] = []
    skipped: list[SkippedDocument] = []

    for content_hash in db.all_content_hashes():
        jobs = db.jobs_for_hash(content_hash)
        if not jobs:
            # A document whose jobs were pruned. Its rows still go; its files are named by
            # markdown_output, so they are still reachable without scanning the outbox.
            outcome = _delete_one(db, storage, content_hash, filename="(no longer listed)")
        else:
            busy = next((job for job in jobs if job.status in CONVERTING_STATUSES), None)
            if busy is not None:
                skipped.append(
                    SkippedDocument(
                        filename=busy.submitted_filename,
                        reason="being converted right now — nothing of it was removed",
                    )
                )
                continue
            outcome = _delete_one(db, storage, content_hash, jobs[0].submitted_filename)

        deleted += 1
        job_ids.extend(outcome.job_ids)
        removed.extend(outcome.removed_files)
        kept.extend(outcome.kept_files)

    logger.warning(
        "everything_deleted documents=%d jobs=%d removed=%d kept=%d skipped=%d",
        deleted,
        len(job_ids),
        len(removed),
        len(kept),
        len(skipped),
    )
    return BulkDeletionResult(
        documents_deleted=deleted,
        job_ids=job_ids,
        removed_files=removed,
        kept_files=kept,
        skipped=skipped,
    )


def _refuse_if_converting(job: ConversionJob, siblings: list[ConversionJob]) -> None:
    """Refuse while a conversion of the document is *with the engine*.

    Not merely the one named: a document can have a finished job and a retry running at the
    same time, and deleting on the strength of the finished one lets the dispatcher write
    the retry's Markdown into an outbox the operator believes they emptied (FR-022).

    `queued` deliberately does not block. A queued job has never been handed to the engine,
    so there is no result on its way back — deleting its row is what removes it from the
    queue. Blocking on `queued` made a job the dispatcher never picked up impossible to
    remove from the page at all, which is the opposite of what the refusal is for.
    """
    if not any(sibling.status in CONVERTING_STATUSES for sibling in siblings):
        return
    raise DeletionRefused(
        f'"{job.submitted_filename}" is being converted right now. '
        "Wait for it to finish, then delete it."
    )
