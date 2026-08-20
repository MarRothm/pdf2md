"""Filesystem operations for the inbox and the outbox.

The outbox is a bind mount the operator opens in Finder, so every write there is
atomic: temp file → `fsync` → rename. A power loss can leave a `.tmp` file behind
but never a truncated `.md` that looks complete (data-model.md).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pdf2md.clock import now, parse_iso
from pdf2md.db import Database
from pdf2md.models import DOWNLOADABLE_STATUSES, IN_FLIGHT_STATUSES, JobStatus

logger = logging.getLogger(__name__)


class OutOfSpaceError(RuntimeError):
    """Raised when a location cannot accept a write; surfaced to the page as 507."""

    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


class Storage:
    def __init__(self, inbox_path: Path, outbox_path: Path) -> None:
        self.inbox_path = Path(inbox_path)
        self.outbox_path = Path(outbox_path)

    def ensure_directories(self) -> None:
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        self.outbox_path.mkdir(parents=True, exist_ok=True)

    # --- inbox ------------------------------------------------------------

    def inbox_file(self, content_hash: str) -> Path:
        return self.inbox_path / f"{content_hash}.pdf"

    def has_inbox_file(self, content_hash: str) -> bool:
        return self.inbox_file(content_hash).is_file()

    def open_inbox_temp(self) -> tuple[int, Path]:
        """A temp file in the inbox, so the final rename never crosses a device."""
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=self.inbox_path, prefix=".upload-", suffix=".part")
        return handle, Path(name)

    def commit_inbox_file(self, temp_path: Path, content_hash: str) -> Path:
        """Move a completed upload into place under its content hash."""
        destination = self.inbox_file(content_hash)
        os.replace(temp_path, destination)
        _fsync_directory(self.inbox_path)
        return destination

    def delete_inbox_file(self, content_hash: str) -> None:
        self.inbox_file(content_hash).unlink(missing_ok=True)

    def part_file(self, content_hash: str, ordinal: int) -> Path:
        """A page-range PDF cut from an over-long upload (FR-034)."""
        return self.inbox_path / f"{content_hash}--part{ordinal:03d}.pdf"

    def delete_part_files(self, content_hash: str) -> None:
        for path in self.inbox_path.glob(f"{content_hash}--part*.pdf"):
            path.unlink(missing_ok=True)

    # --- outbox -----------------------------------------------------------

    def outbox_file(self, output_filename: str) -> Path:
        return self.outbox_path / output_filename

    def has_outbox_file(self, output_filename: str) -> bool:
        return self.outbox_file(output_filename).is_file()

    def write_outbox_atomic(self, output_filename: str, markdown: str) -> int:
        """Write Markdown into the outbox atomically; returns the byte count."""
        self.outbox_path.mkdir(parents=True, exist_ok=True)
        payload = markdown.encode("utf-8")
        destination = self.outbox_file(output_filename)
        handle, temp_name = tempfile.mkstemp(
            dir=self.outbox_path, prefix=f".{output_filename}.", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, destination)
            _fsync_directory(self.outbox_path)
        except OSError:
            temp_path.unlink(missing_ok=True)
            raise
        return len(payload)

    def delete_outbox_file(self, output_filename: str) -> None:
        """Remove a file this service previously wrote.

        Two callers, both narrow on purpose. A document being re-converted replaces its own
        section files, because an engine upgrade can detect different headings and would
        otherwise leave two contradictory versions of the same document for AnythingLLM to
        cite (research.md R13). And an operator deleting a document removes its output
        deliberately, through `delete_outbox_files` below (feature 002, FR-017).

        Neither ever scans the outbox. The only names either passes are names recorded in
        `markdown_output`, so a file this service did not write cannot be removed by it.
        """
        self.outbox_file(output_filename).unlink(missing_ok=True)

    def delete_outbox_files(self, output_filenames: Iterable[str]) -> tuple[list[str], list[str]]:
        """Remove several recorded outputs; returns the names removed and those kept.

        Best effort by design: an unwritable outbox must not abort a deletion halfway and
        leave the operator with no way to finish it. What survived is reported back and
        named in the log (data-model.md INV-5, FR-018).
        """
        removed: list[str] = []
        kept: list[str] = []
        for name in output_filenames:
            # A file already gone is neither removed nor kept: nothing happened to it, and
            # reporting it as removed would credit this deletion with someone else's work.
            if not self.has_outbox_file(name):
                continue
            try:
                self.delete_outbox_file(name)
            except OSError:
                kept.append(name)
            else:
                removed.append(name)
        return removed, kept

    # --- probes -----------------------------------------------------------

    def free_bytes(self, path: Path) -> int | None:
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def is_writable(self, path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=path, prefix=".probe-")
            os.close(handle)
            Path(name).unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def check_space(self, path: Path, required_bytes: int, location: str) -> None:
        free = self.free_bytes(path)
        if free is not None and free < required_bytes:
            raise OutOfSpaceError(location)


def _fsync_directory(path: Path) -> None:
    """Best effort — some filesystems (including bind mounts) refuse this."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def reap_inbox(
    db: Database,
    storage: Storage,
    *,
    retention_hours: float,
    failed_retention_days: float,
) -> list[str]:
    """Delete uploaded PDFs whose retention window has passed.

    Succeeded documents go on the short clock, failed and timed-out ones on the long
    clock so a retry stays possible. A document with any non-terminal job is never
    reaped, whatever its other jobs did (data-model.md).
    """
    reaped: list[str] = []
    moment = now()
    for document in db.documents_with_inbox_file():
        jobs = db.jobs_for_hash(document.content_hash)
        if not jobs or any(job.status in IN_FLIGHT_STATUSES for job in jobs):
            continue

        succeeded = [job for job in jobs if job.status in DOWNLOADABLE_STATUSES and job.ended_at]
        if succeeded:
            deadline_source = max(job.ended_at for job in succeeded if job.ended_at)
            age_limit_hours = retention_hours
        else:
            failed = [
                job
                for job in jobs
                if job.status in (JobStatus.FAILED, JobStatus.TIMED_OUT) and job.ended_at
            ]
            if not failed:
                continue
            deadline_source = max(job.ended_at for job in failed if job.ended_at)
            age_limit_hours = failed_retention_days * 24

        age_hours = (moment - parse_iso(deadline_source)).total_seconds() / 3600
        if age_hours < age_limit_hours:
            continue

        storage.delete_inbox_file(document.content_hash)
        db.clear_inbox_path(document.content_hash)
        reaped.append(document.content_hash)
        logger.info(
            'inbox_reaped content_hash=%s file="%s" age_hours=%.1f',
            document.content_hash[:12],
            document.original_filename,
            age_hours,
        )
    return reaped
