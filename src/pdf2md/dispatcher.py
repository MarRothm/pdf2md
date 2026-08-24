"""The conversion loop.

Claims queued jobs, submits them to the engine, polls, and — in the one step that
must not be interrupted — fetches each result and persists it. Every state change
is logged with the job id and the source filename so a failure can be diagnosed
from the Portainer log view alone (FR-019).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from pathlib import Path

from pdf2md.clock import iso_ago, now, now_iso, parse_iso
from pdf2md.config import Settings
from pdf2md.db import Database
from pdf2md.docling_client import (
    ConversionResult,
    DoclingClient,
    EngineUnavailableError,
    TaskNotFoundError,
    TaskStatus,
)
from pdf2md.images import (
    PendingImage,
    PictureDecision,
    PictureOutcome,
    PlaceholderMismatch,
    image_tokens,
    plan_extraction,
    rewrite_placeholders,
    strip_placeholders,
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
from pdf2md.naming import IMAGE_EXTENSIONS, image_filename, output_filename
from pdf2md.pdfinfo import PdfStructureError, extract_range, plan_parts
from pdf2md.sectioning import section_filename, split_into_sections
from pdf2md.storage import Storage, reap_inbox

logger = logging.getLogger(__name__)

REPEATED_CRASH_REASON = (
    "This document was interrupted and resumed {attempts} times without finishing, so the "
    "service has stopped trying to convert it. It may be too large for the memory this "
    "service is given. Convert it in smaller pieces, or give the service more memory."
)
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
PART_TIMEOUT_REASON = (
    "These pages were still converting after the time limit, even on their own. "
    "Whatever is on them costs far more than the rest of the document."
)
PART_LOST_REASON = (
    "The converter lost these pages before their result could be saved, and they could "
    "not be converted again."
)

MAINTENANCE_INTERVAL_SECONDS = 600
ENGINE_RESTART_WINDOW_MINUTES = 15
ENGINE_RESTART_ALARM = 3
"""Lost tasks within the window before the engine is called what it is: restarting."""


def claim_already_converted(db: Database, storage: Storage, job: ConversionJob) -> str | None:
    """Terminate a job as `already_converted` when its output is already on disk.

    Both the output row and the outbox file must be present: a row alone would send
    the user to a file the operator has since removed (data-model.md, FR-014).

    A file with gaps in it does not count (FR-040). Otherwise a document that converted
    incompletely is answered with that same holed file forever — every re-upload reports
    *Already converted*, and the only way to ask for a whole one is to delete the document
    outright, which throws away the pages that did convert.
    """
    output = db.get_output_for_hash(job.content_hash)
    if output is None or not storage.has_outbox_file(output.output_filename):
        return None
    if output_is_incomplete(db, output.job_id):
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


def output_is_incomplete(db: Database, job_id: str) -> bool:
    """Whether the job that wrote an output reported pages missing from it (FR-040).

    A job pruned from history answers `False`: after the retention period there is nothing
    left to judge by, and re-converting every document whose history aged out would be a
    far worse answer than trusting a file that has been in the outbox for a month.
    """
    producer = db.get_job(job_id)
    if producer is None:
        return False
    return bool(producer.missing_page_ranges) or producer.status is JobStatus.SUCCEEDED_INCOMPLETE


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
        self.last_pass_at: str | None = None
        """When a pass last completed. A loop that has stopped is invisible otherwise."""

        self.last_engine_error: str | None = None
        self.last_engine_error_at: str | None = None
        self.engine_restarts: list[str] = []
        """When the engine was last seen to have forgotten its tasks.

        A task the engine has no record of means it restarted under the work. One is
        unremarkable; a handful in a few minutes is an engine being killed and brought
        back — almost always for memory — and that is invisible from this side otherwise:
        every part simply fails, saying its result was lost."""
        """Why the engine last refused work. A refusal leaves the job queued and is
        retried forever, which is right — but reported as nothing at all it is a document
        that waits indefinitely under a status strip saying the converter is ready."""

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
        self.last_pass_at = now_iso()

    @property
    def is_alive(self) -> bool:
        """Whether the loop is still running. `_loop` survives an ordinary exception, but
        nothing supervises the task itself, and a dead one looks exactly like an idle."""
        return self._task is not None and not self._task.done()

    def note_engine_refusal(self, detail: str) -> None:
        self.last_engine_error = detail
        self.last_engine_error_at = now_iso()

    def note_engine_accepted(self) -> None:
        self.last_engine_error = None
        self.last_engine_error_at = None

    def note_engine_forgot_task(self) -> None:
        """Record that the engine has no record of a task it was given (FR-041)."""
        cutoff = iso_ago(minutes=ENGINE_RESTART_WINDOW_MINUTES)
        self.engine_restarts = [at for at in self.engine_restarts if at >= cutoff]
        self.engine_restarts.append(now_iso())

    @property
    def engine_restarts_recent(self) -> int:
        cutoff = iso_ago(minutes=ENGINE_RESTART_WINDOW_MINUTES)
        return len([at for at in self.engine_restarts if at >= cutoff])

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
            # Counted for every recovery, queued or not. A document that keeps stopping the
            # service is recovered from `queued` each time, so an attempt counter that only
            # moves for started work never notices the loop it is part of (FR-042).
            attempts = job.attempt + 1
            if attempts > self.settings.job_max_attempts:
                self.db.finish_job(
                    job.id,
                    JobStatus.FAILED,
                    failure_reason=REPEATED_CRASH_REASON.format(attempts=job.attempt),
                )
                log_job(
                    logger,
                    "job_failed",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    level=logging.ERROR,
                    outcome="failed",
                    attempt=job.attempt,
                    reason="recovered too many times without finishing; refusing to retry",
                )
                continue

            if self.storage.has_inbox_file(job.content_hash):
                attempt = attempts
                if job.part_count > 1:
                    # Only the unfinished parts go back. A part that already converted
                    # keeps its Markdown rather than being paid for twice.
                    self.db.requeue_unfinished_parts(job.id)
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
                if not self.reconcile_split_job(job):
                    continue
                await self.submit_parts(job, inbox_file)
                continue

            try:
                submitted = await self.engine.submit(
                    job.submitted_filename, inbox_file.read_bytes()
                )
            except EngineUnavailableError as error:
                # The job stays queued; health reports degraded until the engine returns.
                self.note_engine_refusal(str(error))
                logger.warning(
                    'engine_submit_failed job_id=%s file="%s" detail="%s"',
                    job.id,
                    job.submitted_filename,
                    error,
                )
                return

            self.note_engine_accepted()
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
        parts = self.db.part_states_for_job(job.id)
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
                self._log_part_gap(job, part, f"extraction failed: {error}")
                continue
            except EngineUnavailableError as error:
                self.note_engine_refusal(str(error))
                logger.warning(
                    'engine_submit_failed job_id=%s part=%s detail="%s"',
                    job.id,
                    part.ordinal,
                    error,
                )
                return

            self.note_engine_accepted()
            self.db.mark_part_submitted(part.id, submitted.task_id)
            log_job(
                logger,
                "part_submitted",
                job_id=job.id,
                filename=job.submitted_filename,
                part=part.ordinal,
                pages=f"{part.first_page}-{part.last_page}",
                part_bytes=part_path.stat().st_size,
                attempt=part.attempt,
                task_id=submitted.task_id,
            )
            capacity -= 1
            if job.status is JobStatus.QUEUED:
                self.db.mark_submitted(job.id, submitted.task_id, submitted.task_position)
                job = self.db.get_job(job.id) or job

    def reconcile_split_job(self, job: ConversionJob) -> bool:
        """Repair the two states a split job can be left in with nobody watching it.

        `poll_active` only looks at jobs the engine is working on, and a job is only that
        once one of its parts has been submitted. A split job sitting in `queued` with
        parts already in flight is therefore polled by nobody, while `submit_parts` sees
        no capacity and submits nothing — a document that waits for ever, logging nothing,
        under a status line that says the converter is ready. The same is true of a job
        whose parts all failed before it ever left `queued`: every part is terminal and
        the join that would finish the document is only ever called from the polling path.

        Returns True when the job still has work to do.
        """
        if job.part_count < 2 or job.status is not JobStatus.QUEUED:
            return True
        parts = self.db.part_states_for_job(job.id)
        if not parts:
            return True

        in_flight = [
            part for part in parts if part.status in (PartStatus.SUBMITTED, PartStatus.RUNNING)
        ]
        if in_flight:
            self.db.mark_submitted(job.id, in_flight[0].engine_task_id or "", None)
            log_job(
                logger,
                "job_reconciled",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                reason="queued while its parts were already with the engine",
                parts_in_flight=len(in_flight),
            )
            return False

        if all(part.status in TERMINAL_PART_STATUSES for part in parts):
            log_job(
                logger,
                "job_reconciled",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                reason="queued after every part had already finished",
            )
            self.finish_split_job(job)
            return False
        return True

    async def poll_parts(self, job: ConversionJob) -> None:
        """Advance each in-flight part, then finish the job once none are left."""
        for part in self.db.part_states_for_job(job.id):
            if part.status not in (PartStatus.SUBMITTED, PartStatus.RUNNING):
                continue
            assert part.engine_task_id
            try:
                poll = await self.engine.poll(part.engine_task_id)
            except TaskNotFoundError:
                self.note_engine_forgot_task()
                self._retry_part(job, part, PART_LOST_REASON, reason_log="engine forgot the task")
                continue
            except EngineUnavailableError:
                return

            if poll.task_status is TaskStatus.STARTED and part.status is not PartStatus.RUNNING:
                self.db.mark_part_running(part.id)
            elif poll.task_status is TaskStatus.FAILURE:
                await self._part_failed_at_engine(job, part)
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
        for part in self.db.part_states_for_job(job.id):
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
            # A whole document in this position has to be uploaded again, because only the
            # person who sent it can do that. A part does not: its pages are still in the
            # inbox, so the honest response is to convert them again (FR-038).
            self._retry_part(job, part, PART_LOST_REASON, reason_log="result was lost")
            return

        if not result_is_successful(result):
            reason = DoclingClient.failure_reason_from(
                engine_status=result.status, errors=result.errors
            )
            self._retry_part_smaller(job, part, reason, reason_log="engine returned a failure")
            return

        self.db.finish_part(part.id, PartStatus.SUCCEEDED, markdown=result.markdown)
        stored = self._store_part_images(job, part, result.document, result.markdown)
        log_job(
            logger,
            "part_succeeded",
            job_id=job.id,
            filename=job.submitted_filename,
            part=part.ordinal,
            pages=f"{part.first_page}-{part.last_page}",
            # Per part, because a split document counts its pictures only at the join —
            # which for a two-thousand-page document is an hour of no signal at all.
            images=stored,
        )

    # --- keeping a part alive (FR-038) ------------------------------------

    async def _part_failed_at_engine(self, job: ConversionJob, part: ConversionPart) -> None:
        """A part the engine reports as failed: ask why, then try it in smaller pieces.

        The whole-document path already fetches the engine's own words before deciding
        what to tell the user; a part that skipped that step reported every failure as the
        same sentence, which is how a document ends up saying *pages are missing* with no
        way to find out what happened to them.
        """
        errors = await self._part_failure_detail(part)
        reason = DoclingClient.failure_reason_from(engine_status="failure", errors=errors)
        self._retry_part_smaller(job, part, reason, reason_log="engine reported a failure")

    async def _part_failure_detail(self, part: ConversionPart) -> list[str]:
        """The engine's errors for a failed part. Nothing is lost by consuming the result:
        a failed task has no Markdown to serve, and the part is being retried or given up
        on either way (research.md R3)."""
        if not part.engine_task_id:
            return []
        try:
            result = await self.engine.fetch_result(part.engine_task_id)
        except (TaskNotFoundError, EngineUnavailableError):
            return []
        return result.errors

    def _retry_part(
        self, job: ConversionJob, part: ConversionPart, reason: str, *, reason_log: str
    ) -> None:
        """Convert the same pages again, then in smaller pieces, then give up.

        The fall-through matters when the engine is not merely forgetful but is dying on
        this range — an out-of-memory kill takes its whole task table with it, so the
        symptom is a lost task and the cure is a smaller part, not a third identical one.
        """
        if part.attempt >= self.settings.part_max_attempts:
            self._retry_part_smaller(job, part, reason, reason_log=reason_log)
            return
        self.db.requeue_part(part.id)
        log_job(
            logger,
            "part_requeued",
            job_id=job.id,
            filename=job.submitted_filename,
            part=part.ordinal,
            pages=f"{part.first_page}-{part.last_page}",
            attempt=part.attempt + 1,
            reason=reason_log,
        )

    def _retry_part_smaller(
        self,
        job: ConversionJob,
        part: ConversionPart,
        reason: str,
        *,
        reason_log: str,
        status: PartStatus = PartStatus.FAILED,
    ) -> None:
        """Halve a part that could not be converted and try both halves (FR-038).

        This is what turns the failure the operator actually met — every hundred-page part
        of a long scan running past the engine's time ceiling, leaving a document that was
        nothing but gaps — into a document that converts. It is bounded by `split_depth`,
        because each attempt costs another timeout's worth of engine time, and by
        `part_min_pages`, because below that the pages are the problem, not their number.
        """
        pages = part.last_page - part.first_page + 1
        if (
            part.split_depth >= self.settings.part_retry_splits
            or pages <= self.settings.part_min_pages
            or pages < 2
        ):
            self.db.finish_part(part.id, status, failure_reason=reason)
            self._log_part_gap(job, part, reason_log)
            return

        midpoint = part.first_page + pages // 2 - 1
        self.storage.delete_part_file(job.content_hash, part.ordinal)
        self.db.split_part(part.id, [(part.first_page, midpoint), (midpoint + 1, part.last_page)])
        log_job(
            logger,
            "part_halved",
            job_id=job.id,
            filename=job.submitted_filename,
            part=part.ordinal,
            pages=f"{part.first_page}-{part.last_page}",
            reason=reason_log,
        )

    def _log_part_gap(self, job: ConversionJob, part: ConversionPart, reason_log: str) -> None:
        log_job(
            logger,
            "part_failed",
            job_id=job.id,
            filename=job.submitted_filename,
            level=logging.WARNING,
            part=part.ordinal,
            pages=f"{part.first_page}-{part.last_page}",
            attempt=part.attempt,
            outcome="missing",
            reason=reason_log,
        )

    def _all_parts_terminal(self, job_id: str) -> bool:
        parts = self.db.part_states_for_job(job_id)
        return bool(parts) and all(part.status in TERMINAL_PART_STATUSES for part in parts)

    def finish_split_job(self, job: ConversionJob) -> None:
        """Join the parts in order and write the document, gaps and all (FR-035).

        A part that failed does not discard the rest: nineteen good parts are worth more
        than a clean failure, and the gap is named in the file as well as on the page,
        because job history is pruned while the file is forever.
        """
        # The one caller that needs the Markdown itself, and the only place the whole
        # document is held in memory at once.
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

        source = self.db.get_source_document(job.content_hash)
        display_name = source.original_filename if source else job.submitted_filename
        rewritten, images = self._images_from_parts(job, display_name, parts)

        markdown = _join_parts(parts, rewritten)
        self.persist_markdown(
            job,
            markdown,
            engine_status=EngineStatus.SUCCESS,
            missing_ranges=missing or None,
            images=images,
        )
        # Swept only once the pictures are in the outbox: a failure before that leaves the
        # scratch in place, and the document can be converted again from it.
        self.storage.delete_part_files(job.content_hash)

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
            document=result.document,
        )

    # --- pictures (feature 003) -------------------------------------------

    def _store_part_images(
        self, job: ConversionJob, part: ConversionPart, document: dict, result_markdown: str
    ) -> int:
        """Write this part's pictures to scratch and remember the plan (research R6).

        Ordinals are not assigned here. A part knows only its own pictures; where they sit
        in the document is a question only the join can answer, and numbering per part
        would restart at one every forty pages.
        """
        if not self.settings.extract_images or not document:
            return 0
        decisions = plan_extraction(
            document,
            coverage=self.settings.image_page_coverage,
            min_bytes=self.settings.image_min_bytes,
            max_per_document=self.settings.image_max_per_document,
            inline=[uri for _, _, uri in image_tokens(result_markdown)],
        )
        if not decisions:
            return 0

        plan: list[dict] = []
        for index, decision in enumerate(decisions, start=1):
            entry: dict = {"outcome": decision.outcome.value, "page_no": decision.page_no}
            if decision.outcome is PictureOutcome.EXTRACTED:
                assert decision.payload is not None and decision.mimetype is not None
                extension = IMAGE_EXTENSIONS[decision.mimetype]
                scratch = self.storage.part_image_file(
                    job.content_hash, part.ordinal, index, extension
                )
                scratch.write_bytes(decision.payload)
                entry["file"] = scratch.name
                entry["mimetype"] = decision.mimetype
            plan.append(entry)
        self.db.set_part_image_plan(part.id, plan)
        return sum(1 for entry in plan if "file" in entry)

    def _images_from_parts(
        self, job: ConversionJob, display_name: str, parts: list[ConversionPart]
    ) -> tuple[dict[str, str], list[PendingImage]]:
        """Name every part's pictures in document order and rewrite its placeholders.

        Returns the Markdown to use for each succeeded part, and the pictures to move into
        the outbox. The ceiling is applied again here, across the whole document rather
        than per part — forty parts of twenty figures is eight hundred files, and each part
        thought itself well inside the limit.
        """
        rewritten: dict[str, str] = {}
        pending: list[PendingImage] = []
        seen: dict[str, str] = {}
        """Digest to filename, across the whole document. A letterhead on every page of a
        two-thousand-page contract is one picture, not two thousand files."""
        if not self.settings.extract_images:
            return rewritten, pending

        for part in parts:
            if part.status is not PartStatus.SUCCEEDED or part.markdown is None:
                continue
            plan = json.loads(part.image_plan) if part.image_plan else []
            if not plan:
                continue

            decisions: list[PictureDecision] = []
            filenames: list[str | None] = []
            for entry in plan:
                outcome = PictureOutcome(entry["outcome"])
                if outcome is PictureOutcome.EXTRACTED and len(seen) >= (
                    self.settings.image_max_per_document
                ):
                    outcome = PictureOutcome.OVER_CEILING
                decisions.append(PictureDecision(outcome, page_no=entry.get("page_no")))
                if outcome is not PictureOutcome.EXTRACTED:
                    filenames.append(None)
                    continue
                scratch = self.storage.inbox_path / entry["file"]
                try:
                    digest = hashlib.sha256(scratch.read_bytes()).hexdigest()
                except OSError:
                    # The picture this part left behind is gone. Say so and carry on: a
                    # reference to a file that is not there is the one thing FR-003 calls
                    # a defect, so it must not be written.
                    log_job(
                        logger,
                        "image_missing",
                        job_id=job.id,
                        filename=job.submitted_filename,
                        level=logging.WARNING,
                        part=part.ordinal,
                        scratch=entry["file"],
                    )
                    decisions[-1] = PictureDecision(
                        PictureOutcome.UNUSABLE, page_no=entry.get("page_no")
                    )
                    filenames.append(None)
                    continue
                if digest in seen:
                    filenames.append(seen[digest])
                    continue
                ordinal = len(pending) + 1
                name = image_filename(display_name, job.content_hash, ordinal, entry["mimetype"])
                seen[digest] = name
                filenames.append(name)
                pending.append(
                    PendingImage(
                        filename=name,
                        ordinal=ordinal,
                        page_no=entry.get("page_no"),
                        mimetype=entry["mimetype"],
                        source=scratch,
                    )
                )
            try:
                rewritten[part.id] = rewrite_placeholders(part.markdown, decisions, filenames)
            except PlaceholderMismatch as error:
                log_job(
                    logger,
                    "images_skipped",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    level=logging.WARNING,
                    part=part.ordinal,
                    reason=str(error),
                )
                for _ in range(sum(1 for name in filenames if name)):
                    pending.pop()
        return rewritten, pending

    def extract_images(
        self, job: ConversionJob, display_name: str, markdown: str, document: dict
    ) -> tuple[str, list[PendingImage]]:
        """Decide which pictures are figures, name them, and rewrite the Markdown.

        Returns the rewritten Markdown and the pictures to write. Nothing is written here:
        the caller writes them before the Markdown that references them, so a failure
        leaves unreferenced files rather than references to nothing.
        """
        if not self.settings.extract_images:
            # Off means no pictures and no image files (FR-010) — not a comment marker
            # left where each one stood. The engine is still asked for `placeholder` mode,
            # so the markers arrive; they are ours to clear away.
            return strip_placeholders(markdown), []

        if not document:
            # Extraction is on and the engine returned no structure to extract from. The
            # markers would otherwise be shipped to the knowledge base as noise, so they
            # go — but this is said out loud, because it is what a wrong assumption about
            # the engine looks like from here (plan.md, Risks).
            cleaned = strip_placeholders(markdown)
            if cleaned != markdown:
                log_job(
                    logger,
                    "images_unavailable",
                    job_id=job.id,
                    filename=job.submitted_filename,
                    level=logging.WARNING,
                    reason="extraction is on but the engine returned no picture data",
                )
            return cleaned, []

        decisions = plan_extraction(
            document,
            coverage=self.settings.image_page_coverage,
            min_bytes=self.settings.image_min_bytes,
            max_per_document=self.settings.image_max_per_document,
            inline=[uri for _, _, uri in image_tokens(markdown)],
        )
        if not decisions:
            return strip_placeholders(markdown), []

        if all(decision.outcome is PictureOutcome.UNUSABLE for decision in decisions):
            # Every picture in the document, and not one of them had bytes we could read.
            # That is not a property of the document — it is the engine returning a shape
            # this service does not know how to take pictures out of, and it is worth
            # saying so rather than writing a file full of "not extracted" notes.
            log_job(
                logger,
                "images_unusable",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                pictures=len(decisions),
                reason="the engine returned pictures with no readable image data",
            )

        filenames: list[str | None] = []
        pending: list[PendingImage] = []
        seen: dict[str, str] = {}
        for decision in decisions:
            if decision.outcome is not PictureOutcome.EXTRACTED:
                filenames.append(None)
                continue
            assert decision.payload is not None and decision.mimetype is not None
            digest = hashlib.sha256(decision.payload).hexdigest()
            if digest in seen:
                # The same picture again — a letterhead, a logo, a stamp. One file, as
                # many references as there are places it appears (FR-005).
                filenames.append(seen[digest])
                continue
            ordinal = len(pending) + 1
            name = image_filename(display_name, job.content_hash, ordinal, decision.mimetype)
            seen[digest] = name
            filenames.append(name)
            pending.append(
                PendingImage(
                    filename=name,
                    ordinal=ordinal,
                    page_no=decision.page_no,
                    mimetype=decision.mimetype,
                    payload=decision.payload,
                )
            )

        try:
            rewritten = rewrite_placeholders(markdown, decisions, filenames)
        except PlaceholderMismatch as error:
            # Every reference after the discrepancy would name the wrong figure. Keep the
            # placeholders — they carry no picture data either, so FR-001 still holds —
            # and say so rather than writing a document that cites the wrong pictures.
            log_job(
                logger,
                "images_skipped",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                reason=str(error),
            )
            return markdown, []
        return rewritten, pending

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
        document: dict | None = None,
        images: list[PendingImage] | None = None,
    ) -> None:
        """Write a finished document to the outbox and end the job.

        Shared by the whole-document path and the split path, so a document large enough
        for section files gets them either way — FR-033 keys on the size of the Markdown,
        not on whether the PDF happened to need splitting.
        """
        source = self.db.get_source_document(job.content_hash)
        display_name = source.original_filename if source else job.submitted_filename
        page_count = engine_page_count or (source.page_count if source else None)
        if images is None:
            markdown, images = self.extract_images(job, display_name, markdown, document or {})

        if missing_ranges:
            status = JobStatus.SUCCEEDED_INCOMPLETE
        elif self.is_suspect_yield(markdown, page_count):
            status = JobStatus.SUCCEEDED_SUSPECT
        else:
            status = JobStatus.SUCCEEDED

        files = self._plan_output_files(display_name, job.content_hash, markdown)
        previous = {output.output_filename for output in self.db.outputs_for_hash(job.content_hash)}
        superseded = sorted(previous - {name for name, _, _, _ in files})

        previous_images = {
            image.image_filename for image in self.db.images_for_hash(job.content_hash)
        }
        superseded_images = sorted(previous_images - {image.filename for image in images})

        try:
            # Pictures first: a failure then leaves files nothing points at, rather than
            # references pointing at files that were never written (plan.md, Risks).
            stored: list[tuple[str, int, int, int | None, str]] = []
            for image in images:
                payload = image.payload
                if payload is None and image.source is not None:
                    payload = Path(str(image.source)).read_bytes()
                if payload is None:
                    continue
                size_bytes = self.storage.write_outbox_image_atomic(image.filename, payload)
                stored.append(
                    (image.filename, size_bytes, image.ordinal, image.page_no, image.mimetype)
                )

            written: list[tuple[str, int, int | None, str | None]] = []
            for name, text, ordinal, title in files:
                size_bytes = self.storage.write_outbox_atomic(name, text)
                written.append((name, size_bytes, ordinal, title))
            self.db.record_outputs_and_finish(
                job_id=job.id,
                content_hash=job.content_hash,
                outputs=written,
                images=stored,
                engine_status=engine_status,
                status=status,
                engine_errors=errors or None,
                page_count=engine_page_count,
                missing_page_ranges=missing_ranges,
                superseded=superseded,
            )
            for stale in (*superseded, *superseded_images):
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
            images=len(images),
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
            if job.part_count > 1:
                self._expire_part_timeouts(job, limit, moment)
                continue
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

    def _expire_part_timeouts(self, job: ConversionJob, limit: float, moment) -> None:
        """The watchdog is per part for a split document, and it has to be.

        `JOB_TIMEOUT_SECONDS` is one conversion's allowance. Measured from the job's
        creation, a twenty-part document cannot finish inside it — the watchdog would
        terminate every document that splitting exists to rescue, after burning the engine
        time (research.md R12). A document's effective ceiling is therefore its part count
        times this limit, and each part is judged on its own clock.
        """
        for part in self.db.part_states_for_job(job.id):
            if part.status not in (PartStatus.SUBMITTED, PartStatus.RUNNING):
                continue
            started = part.started_at or part.created_at
            age = (moment - parse_iso(started)).total_seconds()
            if age < limit:
                continue
            log_job(
                logger,
                "part_timed_out",
                job_id=job.id,
                filename=job.submitted_filename,
                level=logging.WARNING,
                part=part.ordinal,
                pages=f"{part.first_page}-{part.last_page}",
                age_seconds=round(age),
            )
            self._retry_part_smaller(
                job,
                part,
                PART_TIMEOUT_REASON,
                reason_log="ran out of time",
                status=PartStatus.TIMED_OUT,
            )
        if self._all_parts_terminal(job.id):
            self.finish_split_job(job)

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


def _join_parts(parts: list[ConversionPart], rewritten: dict[str, str] | None = None) -> str:
    """Concatenate the parts in reading order, marking any gap where it falls.

    The marker goes in the Markdown itself, not only on the page: job history is pruned
    after a documented retention period while the outbox is the durable record, so a
    warning that lives only in the job list disappears while the incomplete file stays in
    the knowledge base forever (FR-035).
    """
    pieces: list[str] = []
    # By first page, not by ordinal: a part halved after running out of time appends its
    # replacements at the end of the table, and they belong where their pages are (FR-038).
    for part in sorted(parts, key=lambda part: part.first_page):
        if part.status is PartStatus.SUCCEEDED and part.markdown is not None:
            pieces.append((rewritten or {}).get(part.id, part.markdown))
        else:
            pieces.append(
                f"\n\n> **Pages {part.first_page}-{part.last_page} are missing from this "
                f"document.** That part could not be converted, so its content is absent "
                f"here. Convert those pages separately if you need them.\n\n"
            )
    return "\n\n".join(piece.strip("\n") for piece in pieces if piece) + "\n"


def result_is_successful(result: ConversionResult) -> bool:
    return result.status in (EngineStatus.SUCCESS, EngineStatus.PARTIAL_SUCCESS)
