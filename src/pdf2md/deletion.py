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
from pdf2md.models import CONVERTING_STATUSES, ConversionJob, DeletionResult
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

    # Only names this service recorded as its own output. Never a directory scan, never a
    # path from the request (INV-2).
    output_filenames = [output.output_filename for output in db.outputs_for_hash(job.content_hash)]

    removed, kept = storage.delete_outbox_files(output_filenames)

    upload_discarded = storage.has_inbox_file(job.content_hash)
    storage.delete_inbox_file(job.content_hash)
    storage.delete_part_files(job.content_hash)

    job_ids = db.delete_document_rows(job.content_hash)

    logger.info(
        'document_deleted file="%s" content_hash=%s jobs=%d removed=%d kept=%s upload=%s',
        job.submitted_filename,
        job.content_hash[:12],
        len(job_ids),
        len(removed),
        ",".join(kept) or "-",
        "discarded" if upload_discarded else "already gone",
    )
    for name in kept:
        logger.warning('output_not_removed file="%s" content_hash=%s', name, job.content_hash[:12])

    return DeletionResult(
        job_ids=job_ids,
        filename=job.submitted_filename,
        removed_files=removed,
        kept_files=kept,
        upload_discarded=upload_discarded,
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
