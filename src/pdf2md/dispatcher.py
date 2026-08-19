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
from pdf2md.models import ConversionJob, EngineStatus, JobStatus
from pdf2md.naming import output_filename
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

    # --- polling ----------------------------------------------------------

    async def poll_active(self) -> None:
        for job in self.db.active_jobs():
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

        document = self.db.get_source_document(job.content_hash)
        display_name = document.original_filename if document else job.submitted_filename
        name = output_filename(display_name, job.content_hash)
        page_count = result.page_count or (document.page_count if document else None)
        status = (
            JobStatus.SUCCEEDED_SUSPECT
            if self.is_suspect_yield(result.markdown, page_count)
            else JobStatus.SUCCEEDED
        )

        try:
            size_bytes = self.storage.write_outbox_atomic(name, result.markdown)
            self.db.record_output_and_finish(
                job_id=job.id,
                content_hash=job.content_hash,
                output_filename=name,
                size_bytes=size_bytes,
                engine_status=result.status,
                status=status,
                engine_errors=result.errors or None,
                page_count=result.page_count,
            )
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
            output=name,
            output_bytes=size_bytes,
            engine_status=result.status,
            processing_seconds=result.processing_time,
        )

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


def result_is_successful(result: ConversionResult) -> bool:
    return result.status in (EngineStatus.SUCCESS, EngineStatus.PARTIAL_SUCCESS)
