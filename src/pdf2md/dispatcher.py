"""The conversion loop.

Claims queued jobs, submits them to the engine, polls, and — in the one step that
must not be interrupted — fetches each result and persists it. Every state change
is logged with the job id and the source filename so a failure can be diagnosed
from the Portainer log view alone (FR-019).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from pdf2md.clock import iso_ago, now, parse_iso
from pdf2md.config import Settings
from pdf2md.db import Database
from pdf2md.docling_client import (
    ConversionResult,
    DoclingClient,
    EngineUnavailableError,
    TaskNotFoundError,
    TaskStatus,
)
from pdf2md.logging_config import log_job
from pdf2md.models import (
    TERMINAL_PART_STATUSES,
    ConversionJob,
    ConversionPart,
    EngineStatus,
    JobStatus,
    PartStatus,
)
from pdf2md.naming import output_filename
from pdf2md.pdfinfo import PdfStructureError, extract_range, plan_parts
from pdf2md.sectioning import section_filename, split_into_sections
from pdf2md.storage import Storage, reap_inbox

logger = logging.getLogger(__name__)

RESTART_LOST_FILE_REASON = (
    "This document was interrupted by a restart and the uploaded file is no longer "
    "available. Please upload it again."
)
MISSING_UPLOAD_REASON = (
    "The uploaded file is no longer available on the server. Please upload it again."
)
LOST_RESULT_REASON = (
    "The document was converted but the result could not be saved, so it was lost. "
    "Please convert it again."
)
TIMEOUT_REASON_TEMPLATE = (
    "This document was still converting after {minutes} minutes and was stopped. "
    "It may be very large or unusually complex."
)

MAINTENANCE_INTERVAL_SECONDS = 600


def claim_already_converted(db: Database, storage: Storage, job: ConversionJob) -> str | None:
    """Terminate a job as `already_converted` when its output is already on disk.

    Both the output row and the outbox file must be present: a row alone would send
    the user to a file the operator has since removed (data-model.md, FR-014).
    """
    output = db.get_output_for_hash(job.content_hash)
    if output is None or not storage.has_outbox_file(output.output_filename):
        return None
    db.finish_job(job.id, JobStatus.ALREADY_CONVERTED, output_filename=output.output_filename)
    log_job(
        logger,
        "job_already_converted",
        job_id=job.id,
        filename=job.submitted_filename,
        outcome="already_converted",
        output=output.output_filename,
    )
    return output.output_filename


class Dispatcher:
    def __init__(
        self,
        *,
        db: Database,
        storage: Storage,
        engine: DoclingClient,
        settings: Settings,
    ) -> None:
        self.db = db
        self.storage = storage
        self.engine = engine
        self.settings = settings
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._last_maintenance = now()

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        self.recover_in_flight()
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="pdf2md-dispatcher")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
                self.run_maintenance()
            except Exception:  # never let one bad pass kill the loop
                logger.exception("dispatcher_pass_failed")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    # --- one pass ---------------------------------------------------------

    async def run_once(self) -> None:
        self.expire_timeouts()
        await self.poll_active()
        await self.submit_queued()

    async def drain(self, max_passes: int = 500) -> None:
        """Run passes until nothing is in flight — the test harness's clock."""
        for _ in range(max_passes):
            if not self.db.in_flight_jobs():
                return
            await self.run_once()
        raise AssertionError("jobs were still in flight after the maximum number of passes")

    # --- restart recovery (data-model.md restart rules) --------------------

    def recover_in_flight(self) -> None:
        """Leave nothing non-terminal behind after a restart.

        Engine task ids do not survive an engine restart, so a job is resubmitted from
        the inbox rather than polled. When its PDF is gone, the job is reported failed
        instead of being silently dropped.
        """
        for job in self.db.in_flight_jobs():
            if self.storage.has_inbox_file(job.content_hash):
                attempt = job.attempt + 1 if job.status is not JobStatus.QUEUED else job.attempt
                self.db.requeue(job.id, attempt=attempt)
                log_job(
                    logger,
                    "job_recovered",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    outcome="requeued",
                    attempt=attempt,
                    previous_status=job.status.value,
                )
            else:
                self.db.finish_job(
                    job.id, JobStatus.FAILED, failure_reason=RESTART_LOST_FILE_REASON
                )
                log_job(
                    logger,
                    "job_failed",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    level=logging.WARNING,
                    outcome="failed",
                    reason="restart, uploaded file missing",
                )

    # --- submission -------------------------------------------------------

    async def submit_queued(self) -> None:
        """Submit at most `max_in_flight` documents, so a big batch queues (FR-027)."""
        capacity = self.settings.max_in_flight - self.db.count_active()
        for job in self.db.next_queued_jobs(capacity):
            if claim_already_converted(self.db, self.storage, job):
                continue

            inbox_file = self.storage.inbox_file(job.content_hash)
            if not inbox_file.is_file():
                self.db.finish_job(job.id, JobStatus.FAILED, failure_reason=MISSING_UPLOAD_REASON)
                log_job(
                    logger,
                    "job_failed",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    level=logging.WARNING,
                    outcome="failed",
                    reason="uploaded file missing from the inbox",
                )
                continue

            if self._needs_parts(job):
                await self.submit_parts(job, inbox_file)
                continue

            try:
                submitted = await self.engine.submit(
                    job.submitted_filename, inbox_file.read_bytes()
                )
            except EngineUnavailableError as error:
                # The job stays queued; health reports degraded until the engine returns.
                logger.warning(
                    'engine_submit_failed job_id=%s file="%s" detail="%s"',
                    job.id,
                    job.submitted_filename,
                    error,
                )
                return

            self.db.mark_submitted(job.id, submitted.task_id, submitted.task_position)
            log_job(
                logger,
                "job_submitted",
                job_id=job.id,
                filename=job.submitted_filename,
                task_id=submitted.task_id,
                attempt=job.attempt,
                queue_position=submitted.task_position,
            )

    # --- splitting (FR-034) -----------------------------------------------

    def _needs_parts(self, job: ConversionJob) -> bool:
        """True once this document is known to be longer than one part."""
        if job.part_count > 1:
            return True
        document = self.db.get_source_document(job.content_hash)
        pages = document.page_count if document else None
        return bool(pages and pages > self.settings.part_max_pages)

    async def submit_parts(self, job: ConversionJob, inbox_file: Path) -> None:
        """Cut the document into page ranges if needed, then feed the engine a few.

        Only `parts_in_flight` parts of one document are in the engine at a time. The
        page ceiling bounds the worst case, but this is what stops a long document from
        sitting in front of everyone else's short ones for hours (research.md R14).
        """
        parts = self.db.parts_for_job(job.id)
        if not parts:
            document = self.db.get_source_document(job.content_hash)
            pages = document.page_count if document else None
            if not pages:
                return
            parts = self.db.create_parts(job.id, plan_parts(pages, self.settings.part_max_pages))
            log_job(
                logger,
                "job_split",
                job_id=job.id,
                filename=job.submitted_filename,
                pages=pages,
                parts=len(parts),
            )

        capacity = self.settings.parts_in_flight - self.db.count_parts_in_flight(job.id)
        for part in parts:
            if capacity <= 0:
                break
            if part.status is not PartStatus.QUEUED:
                continue

            part_path = self.storage.part_file(job.content_hash, part.ordinal)
            try:
                if not part_path.is_file():
                    extract_range(inbox_file, part_path, part.first_page, part.last_page)
                self.db.set_part_path(part.id, str(part_path))
                submitted = await self.engine.submit(
                    f"{job.submitted_filename} (pages {part.first_page}-{part.last_page})",
                    part_path.read_bytes(),
                )
            except PdfStructureError as error:
                self.db.finish_part(
                    part.id,
                    PartStatus.FAILED,
                    failure_reason=f"pages could not be extracted: {error}",
                )
                continue
            except EngineUnavailableError as error:
                logger.warning(
                    'engine_submit_failed job_id=%s part=%s detail="%s"',
                    job.id,
                    part.ordinal,
                    error,
                )
                return

            self.db.mark_part_submitted(part.id, submitted.task_id)
            capacity -= 1
            if job.status is JobStatus.QUEUED:
                self.db.mark_submitted(job.id, submitted.task_id, submitted.task_position)
                job = self.db.get_job(job.id) or job

    async def poll_parts(self, job: ConversionJob) -> None:
        """Advance each in-flight part, then finish the job once none are left."""
        for part in self.db.parts_for_job(job.id):
            if part.status not in (PartStatus.SUBMITTED, PartStatus.RUNNING):
                continue
            assert part.engine_task_id
            try:
                poll = await self.engine.poll(part.engine_task_id)
            except TaskNotFoundError:
                self.db.finish_part(
                    part.id, PartStatus.FAILED, failure_reason="the engine forgot this task"
                )
                continue
            except EngineUnavailableError:
                return

            if poll.task_status is TaskStatus.STARTED and part.status is not PartStatus.RUNNING:
                self.db.mark_part_running(part.id)
            elif poll.task_status is TaskStatus.FAILURE:
                self.db.finish_part(
                    part.id, PartStatus.FAILED, failure_reason="the engine reported a failure"
                )
            elif poll.task_status is TaskStatus.SUCCESS:
                await self._fetch_part(job, part)

        if self._all_parts_terminal(job.id):
            self.finish_split_job(job)
            return

        # Top up. A split job leaves the `queued` state as soon as its first part goes to
        # the engine, so `submit_queued` never revisits it — without this the parts held
        # back by the in-flight cap would wait forever.
        inbox_file = self.storage.inbox_file(job.content_hash)
        if inbox_file.is_file():
            await self.submit_parts(self.db.get_job(job.id) or job, inbox_file)
            return

        # The upload is gone, so no further page range can be cut from it. Fail what is
        # left rather than hanging: the document still finishes, incomplete and saying so.
        for part in self.db.parts_for_job(job.id):
            if part.status is PartStatus.QUEUED:
                self.db.finish_part(
                    part.id, PartStatus.FAILED, failure_reason=RESTART_LOST_FILE_REASON
                )
        if self._all_parts_terminal(job.id):
            self.finish_split_job(job)

    async def _fetch_part(self, job: ConversionJob, part: ConversionPart) -> None:
        """Fetch one part's result and commit it in the same step (research.md R3)."""
        assert part.engine_task_id
        try:
            result = await self.engine.fetch_result(part.engine_task_id)
        except (EngineUnavailableError, TaskNotFoundError):
            self.db.finish_part(part.id, PartStatus.FAILED, failure_reason=LOST_RESULT_REASON)
            return

        if not result_is_successful(result):
            reason = DoclingClient.failure_reason_from(
                engine_status=result.status, errors=result.errors
            )
            self.db.finish_part(part.id, PartStatus.FAILED, failure_reason=reason)
            return

        self.db.finish_part(part.id, PartStatus.SUCCEEDED, markdown=result.markdown)
        log_job(
            logger,
            "part_succeeded",
            job_id=job.id,
            filename=job.submitted_filename,
            part=part.ordinal,
            pages=f"{part.first_page}-{part.last_page}",
        )

    def _all_parts_terminal(self, job_id: str) -> bool:
        parts = self.db.parts_for_job(job_id)
        return bool(parts) and all(part.status in TERMINAL_PART_STATUSES for part in parts)

    def finish_split_job(self, job: ConversionJob) -> None:
        """Join the parts in order and write the document, gaps and all (FR-035).

        A part that failed does not discard the rest: nineteen good parts are worth more
        than a clean failure, and the gap is named in the file as well as on the page,
        because job history is pruned while the file is forever.
        """
        parts = self.db.parts_for_job(job.id)
        succeeded = [part for part in parts if part.status is PartStatus.SUCCEEDED]
        missing = [
            (part.first_page, part.last_page)
            for part in parts
            if part.status is not PartStatus.SUCCEEDED
        ]

        if not succeeded:
            reason = parts[0].failure_reason if parts else None
            self.db.finish_job(
                job.id, JobStatus.FAILED, failure_reason=reason or LOST_RESULT_REASON
            )
            log_job(
                logger,
                "job_failed",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                outcome="failed",
                reason="every part of this document failed",
            )
            return

        markdown = _join_parts(parts)
        self.storage.delete_part_files(job.content_hash)
        self.persist_markdown(
            job,
            markdown,
            engine_status=EngineStatus.SUCCESS,
            missing_ranges=missing or None,
        )

    # --- polling ----------------------------------------------------------

    async def poll_active(self) -> None:
        for job in self.db.active_jobs():
            if job.part_count > 1:
                await self.poll_parts(job)
                continue
            if not job.engine_task_id:
                continue
            try:
                poll = await self.engine.poll(job.engine_task_id)
            except TaskNotFoundError:
                self._handle_forgotten_task(job)
                continue
            except EngineUnavailableError as error:
                logger.warning(
                    'engine_poll_failed job_id=%s file="%s" detail="%s"',
                    job.id,
                    job.submitted_filename,
                    error,
                )
                return

            if poll.task_status is TaskStatus.PENDING:
                self.db.set_queue_position(job.id, poll.task_position)
            elif poll.task_status is TaskStatus.STARTED:
                if job.status is not JobStatus.RUNNING:
                    self.db.mark_running(job.id)
                    log_job(
                        logger,
                        "job_running",
                        job_id=job.id,
                        filename=job.submitted_filename,
                        task_id=job.engine_task_id,
                    )
            elif poll.task_status is TaskStatus.FAILURE:
                errors = await self._failure_detail(job)
                reason = DoclingClient.failure_reason_from(engine_status="failure", errors=errors)
                self.db.finish_job(
                    job.id, JobStatus.FAILED, failure_reason=reason, engine_errors=errors
                )
                log_job(
                    logger,
                    "job_failed",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    level=logging.WARNING,
                    outcome="failed",
                    reason="engine reported failure",
                )
            else:
                await self.fetch_and_persist(job)

    async def _failure_detail(self, job: ConversionJob) -> list[str]:
        """The engine's own words for why a task failed, so the message can be specific.

        Without these, `failure_reason_from` sees only the word "failure" and every
        engine-level failure reads as "the PDF is probably damaged" — including the ones
        that have an accurate message waiting for them, such as exceeding the page limit
        (FR-011).

        Consuming the single-use result here costs nothing: a failed task has no
        `md_content` to lose and the job is terminal either way, so this is the one place
        the hazard in research.md R3 does not apply.
        """
        if not job.engine_task_id:
            return []
        try:
            result = await self.engine.fetch_result(job.engine_task_id)
        except (TaskNotFoundError, EngineUnavailableError):
            # No detail available. The generic message is then the honest one.
            return []
        return result.errors

    def _handle_forgotten_task(self, job: ConversionJob) -> None:
        if self.storage.has_inbox_file(job.content_hash):
            self.db.requeue(job.id, attempt=job.attempt + 1)
            log_job(
                logger,
                "job_recovered",
                job_id=job.id,
                filename=job.submitted_filename,
                outcome="requeued",
                reason="engine no longer knows this task",
                attempt=job.attempt + 1,
            )
        else:
            self.db.finish_job(job.id, JobStatus.FAILED, failure_reason=RESTART_LOST_FILE_REASON)
            log_job(
                logger,
                "job_failed",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                outcome="failed",
                reason="engine no longer knows this task and the upload is gone",
            )

    # --- fetch and persist, as one step -----------------------------------

    async def fetch_and_persist(self, job: ConversionJob) -> None:
        """Fetch the result once and commit it, or fail the job saying so.

        The engine serves each result exactly once (research.md R3). Between the fetch
        and the commit there is nothing to fall back on, so any error here ends the job
        as `failed` with a reason naming the lost result — never left running, which
        would poll forever against a task whose result is gone.
        """
        assert job.engine_task_id
        try:
            result = await self.engine.fetch_result(job.engine_task_id)
        except EngineUnavailableError as error:
            self.db.finish_job(job.id, JobStatus.FAILED, failure_reason=LOST_RESULT_REASON)
            log_job(
                logger,
                "job_failed",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.ERROR,
                outcome="failed",
                reason=f"result fetch failed: {error}",
            )
            return

        if result.status not in (EngineStatus.SUCCESS, EngineStatus.PARTIAL_SUCCESS):
            reason = DoclingClient.failure_reason_from(
                engine_status=result.status, errors=result.errors
            )
            self.db.finish_job(
                job.id, JobStatus.FAILED, failure_reason=reason, engine_errors=result.errors
            )
            log_job(
                logger,
                "job_failed",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                outcome="failed",
                engine_status=result.status,
                engine_errors="; ".join(result.errors) or None,
            )
            return

        self.persist_markdown(
            job,
            result.markdown,
            engine_status=result.status,
            errors=result.errors,
            engine_page_count=result.page_count,
            processing_time=result.processing_time,
        )

    # --- writing the output, however many files it turns out to be --------

    def persist_markdown(
        self,
        job: ConversionJob,
        markdown: str,
        *,
        engine_status: str,
        errors: list[str] | None = None,
        engine_page_count: int | None = None,
        processing_time: float | None = None,
        missing_ranges: list[tuple[int, int]] | None = None,
    ) -> None:
        """Write a finished document to the outbox and end the job.

        Shared by the whole-document path and the split path, so a document large enough
        for section files gets them either way — FR-033 keys on the size of the Markdown,
        not on whether the PDF happened to need splitting.
        """
        document = self.db.get_source_document(job.content_hash)
        display_name = document.original_filename if document else job.submitted_filename
        page_count = engine_page_count or (document.page_count if document else None)

        if missing_ranges:
            status = JobStatus.SUCCEEDED_INCOMPLETE
        elif self.is_suspect_yield(markdown, page_count):
            status = JobStatus.SUCCEEDED_SUSPECT
        else:
            status = JobStatus.SUCCEEDED

        files = self._plan_output_files(display_name, job.content_hash, markdown)
        previous = {output.output_filename for output in self.db.outputs_for_hash(job.content_hash)}
        superseded = sorted(previous - {name for name, _, _, _ in files})

        try:
            written: list[tuple[str, int, int | None, str | None]] = []
            for name, text, ordinal, title in files:
                size_bytes = self.storage.write_outbox_atomic(name, text)
                written.append((name, size_bytes, ordinal, title))
            self.db.record_outputs_and_finish(
                job_id=job.id,
                content_hash=job.content_hash,
                outputs=written,
                engine_status=engine_status,
                status=status,
                engine_errors=errors or None,
                page_count=engine_page_count,
                missing_page_ranges=missing_ranges,
                superseded=superseded,
            )
            for stale in superseded:
                self.storage.delete_outbox_file(stale)
        except Exception:
            self.db.finish_job(job.id, JobStatus.FAILED, failure_reason=LOST_RESULT_REASON)
            log_job(
                logger,
                "job_failed",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.ERROR,
                outcome="failed",
                reason="result could not be persisted; the conversion was lost",
            )
            logger.exception("persist_failed job_id=%s", job.id)
            return

        log_job(
            logger,
            "job_succeeded",
            job_id=job.id,
            filename=job.submitted_filename,
            outcome=status.value,
            output=written[0][0],
            output_bytes=sum(size for _, size, _, _ in written),
            output_files=len(written),
            engine_status=engine_status,
            processing_seconds=processing_time,
        )

    def _plan_output_files(
        self, display_name: str, content_hash: str, markdown: str
    ) -> list[tuple[str, str, int | None, str | None]]:
        """(filename, text, section ordinal, section title) for each file to write.

        One file, or one per section once the Markdown is large enough (FR-033).
        """
        whole = [(output_filename(display_name, content_hash), markdown, None, None)]
        if len(markdown.encode("utf-8")) <= self.settings.section_split_threshold_bytes:
            return whole

        sections = split_into_sections(
            markdown,
            min_bytes=self.settings.section_min_bytes,
            max_bytes=self.settings.section_max_bytes,
        )
        if len(sections) < 2:
            # Nothing to divide on. One large file beats one large file wearing a section
            # name that pretends it is part of a set.
            return whole

        return [
            (
                section_filename(display_name, content_hash, section),
                section.markdown,
                section.ordinal,
                section.title or None,
            )
            for section in sections
        ]

    def is_suspect_yield(self, markdown: str, page_count: int | None) -> bool:
        """Flag a conversion whose yield is implausibly small for the source (FR-029)."""
        characters = len(markdown.strip())
        if page_count and page_count > 0:
            threshold = page_count * self.settings.suspect_min_chars_per_page
        else:
            threshold = self.settings.suspect_min_chars_floor
        return characters < threshold

    # --- watchdog and retention ------------------------------------------

    def expire_timeouts(self) -> None:
        """Stop a job that has outrun `JOB_TIMEOUT_SECONDS` (FR-028).

        The engine's own `MAX_DOCUMENT_TIMEOUT` is set lower, so this normally never
        fires; when it does, the queue keeps moving and no partial Markdown is written.

        Only jobs the engine has actually accepted are subject to it (data-model.md): a
        job still waiting its turn behind a long queue has not overrun anything, and
        failing it would punish a document for the backlog ahead of it.
        """
        limit = self.settings.job_timeout_seconds
        moment = now()
        for job in self.db.active_jobs():
            age = (moment - parse_iso(job.created_at)).total_seconds()
            if age < limit:
                continue
            reason = TIMEOUT_REASON_TEMPLATE.format(minutes=round(limit / 60))
            self.db.finish_job(job.id, JobStatus.TIMED_OUT, failure_reason=reason)
            log_job(
                logger,
                "job_timed_out",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                outcome="timed_out",
                age_seconds=round(age),
            )

    def run_maintenance(self, force: bool = False) -> None:
        """Reap spent uploads and prune old history, at most every few minutes."""
        elapsed = (now() - self._last_maintenance).total_seconds()
        if not force and elapsed < MAINTENANCE_INTERVAL_SECONDS:
            return
        self._last_maintenance = now()
        reap_inbox(
            self.db,
            self.storage,
            retention_hours=self.settings.inbox_retention_hours,
            failed_retention_days=self.settings.failed_inbox_retention_days,
        )
        pruned = self.db.prune_history(before=iso_ago(days=self.settings.job_history_days))
        if pruned:
            logger.info("history_pruned jobs=%d", pruned)


def _join_parts(parts: list[ConversionPart]) -> str:
    """Concatenate the parts in reading order, marking any gap where it falls.

    The marker goes in the Markdown itself, not only on the page: job history is pruned
    after a documented retention period while the outbox is the durable record, so a
    warning that lives only in the job list disappears while the incomplete file stays in
    the knowledge base forever (FR-035).
    """
    pieces: list[str] = []
    for part in sorted(parts, key=lambda part: part.ordinal):
        if part.status is PartStatus.SUCCEEDED and part.markdown is not None:
            pieces.append(part.markdown)
        else:
            pieces.append(
                f"\n\n> **Pages {part.first_page}-{part.last_page} are missing from this "
                f"document.** That part could not be converted, so its content is absent "
                f"here. Convert those pages separately if you need them.\n\n"
            )
    return "\n\n".join(piece.strip("\n") for piece in pieces if piece) + "\n"


def result_is_successful(result: ConversionResult) -> bool:
    return result.status in (EngineStatus.SUCCESS, EngineStatus.PARTIAL_SUCCESS)
