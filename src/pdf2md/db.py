"""SQLite job registry.

The schema is the one in `data-model.md`. There is a single writer — this
service — so no external database is needed at this scale. Every connection sets
`journal_mode=WAL` and `foreign_keys=ON`; WAL is safe only because the database
lives on a named volume rather than a macOS bind mount (research.md R7).

One addition to the data-model tables: `conversion_job.updated_at`. The contract's
`GET /api/jobs?since=` needs a change marker, and states such as `submitted` carry
no timestamp column of their own, so a job entering `Converting` would otherwise be
invisible to an incremental poll.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf2md.clock import now_iso
from pdf2md.models import (
    CONVERTING_STATUSES,
    IN_FLIGHT_STATUSES,
    Backlog,
    ConversionJob,
    ConversionPart,
    JobStatus,
    MarkdownOutput,
    PartStatus,
    SourceDocument,
)

SCHEMA: list[tuple[str, str]] = [
    (
        "001_initial",
        """
        CREATE TABLE batch (
          id              TEXT PRIMARY KEY,
          created_at      TEXT NOT NULL,
          document_count  INTEGER NOT NULL,
          submitter_note  TEXT
        );

        CREATE TABLE source_document (
          content_hash      TEXT PRIMARY KEY,
          original_filename TEXT NOT NULL,
          size_bytes        INTEGER NOT NULL,
          page_count        INTEGER,
          first_seen_at     TEXT NOT NULL,
          inbox_path        TEXT
        );

        CREATE TABLE markdown_output (
          output_filename TEXT PRIMARY KEY,
          content_hash    TEXT NOT NULL REFERENCES source_document(content_hash),
          job_id          TEXT NOT NULL,
          bytes           INTEGER NOT NULL,
          written_at      TEXT NOT NULL,
          engine_status   TEXT NOT NULL
        );

        CREATE TABLE conversion_job (
          id                 TEXT PRIMARY KEY,
          batch_id           TEXT REFERENCES batch(id) ON DELETE SET NULL,
          content_hash       TEXT NOT NULL REFERENCES source_document(content_hash),
          submitted_filename TEXT NOT NULL,
          status             TEXT NOT NULL CHECK (status IN
                               ('queued','submitted','running','succeeded','succeeded_suspect',
                                'already_converted','failed','timed_out')),
          engine_task_id     TEXT,
          queue_position     INTEGER,
          created_at         TEXT NOT NULL,
          updated_at         TEXT NOT NULL,
          started_at         TEXT,
          ended_at           TEXT,
          attempt            INTEGER NOT NULL DEFAULT 1,
          failure_reason     TEXT,
          engine_errors      TEXT,
          output_filename    TEXT REFERENCES markdown_output(output_filename)
        );

        CREATE INDEX idx_job_status  ON conversion_job(status);
        CREATE INDEX idx_job_created ON conversion_job(created_at DESC);
        CREATE INDEX idx_job_batch   ON conversion_job(batch_id);
        CREATE INDEX idx_job_updated ON conversion_job(updated_at DESC);
        CREATE INDEX idx_output_hash ON markdown_output(content_hash);
        """,
    ),
    (
        "002_splitting",
        """
        CREATE TABLE conversion_part (
          id             TEXT PRIMARY KEY,
          job_id         TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
          ordinal        INTEGER NOT NULL,
          first_page     INTEGER NOT NULL,
          last_page      INTEGER NOT NULL,
          part_path      TEXT,
          status         TEXT NOT NULL,
          engine_task_id TEXT,
          markdown       TEXT,
          failure_reason TEXT,
          created_at     TEXT NOT NULL,
          started_at     TEXT,
          ended_at       TEXT,
          UNIQUE (job_id, ordinal)
        );

        CREATE INDEX idx_part_job    ON conversion_part(job_id);
        CREATE INDEX idx_part_status ON conversion_part(status);

        ALTER TABLE markdown_output ADD COLUMN section_ordinal INTEGER;
        ALTER TABLE markdown_output ADD COLUMN section_title TEXT;

        ALTER TABLE conversion_job ADD COLUMN part_count INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE conversion_job ADD COLUMN parts_completed INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE conversion_job ADD COLUMN missing_page_ranges TEXT;
        """,
    ),
    (
        # `succeeded_incomplete` has to join the status CHECK, and SQLite cannot alter a
        # constraint in place — the table is rebuilt. Existing history is copied across
        # rather than dropped, because job history survives a redeploy (FR-017).
        "003_incomplete_status",
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE conversion_job_rebuilt (
          id                  TEXT PRIMARY KEY,
          batch_id            TEXT REFERENCES batch(id) ON DELETE SET NULL,
          content_hash        TEXT NOT NULL REFERENCES source_document(content_hash),
          submitted_filename  TEXT NOT NULL,
          status              TEXT NOT NULL CHECK (status IN
                                ('queued','submitted','running','succeeded','succeeded_suspect',
                                 'succeeded_incomplete','already_converted','failed','timed_out')),
          engine_task_id      TEXT,
          queue_position      INTEGER,
          created_at          TEXT NOT NULL,
          updated_at          TEXT NOT NULL,
          started_at          TEXT,
          ended_at            TEXT,
          attempt             INTEGER NOT NULL DEFAULT 1,
          failure_reason      TEXT,
          engine_errors       TEXT,
          output_filename     TEXT REFERENCES markdown_output(output_filename),
          part_count          INTEGER NOT NULL DEFAULT 1,
          parts_completed     INTEGER NOT NULL DEFAULT 0,
          missing_page_ranges TEXT
        );

        INSERT INTO conversion_job_rebuilt
          SELECT id, batch_id, content_hash, submitted_filename, status, engine_task_id,
                 queue_position, created_at, updated_at, started_at, ended_at, attempt,
                 failure_reason, engine_errors, output_filename, part_count,
                 parts_completed, missing_page_ranges
          FROM conversion_job;

        DROP TABLE conversion_job;
        ALTER TABLE conversion_job_rebuilt RENAME TO conversion_job;

        CREATE INDEX idx_job_status  ON conversion_job(status);
        CREATE INDEX idx_job_created ON conversion_job(created_at DESC);
        CREATE INDEX idx_job_batch   ON conversion_job(batch_id);
        CREATE INDEX idx_job_updated ON conversion_job(updated_at DESC);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        # A part is no longer given up on at the first failure (FR-038), so it needs to
        # carry how many times it has been tried and how far its page range has already
        # been halved. Existing rows default to a first attempt that was never halved,
        # which is what they are.
        "004_part_retries",
        """
        ALTER TABLE conversion_part ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE conversion_part ADD COLUMN split_depth INTEGER NOT NULL DEFAULT 0;
        """,
    ),
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # --- connections ------------------------------------------------------

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """A connection with our pragmas, committing on success."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level="DEFERRED")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        """Apply pending migrations. Idempotent — safe on every start."""
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration ("
                " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migration")}
            for name, ddl in SCHEMA:
                if name in applied:
                    continue
                conn.executescript(ddl)
                conn.execute(
                    "INSERT INTO schema_migration (name, applied_at) VALUES (?, ?)",
                    (name, now_iso()),
                )

    def writable(self) -> bool:
        try:
            with self.connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS write_probe (id INTEGER PRIMARY KEY)")
                conn.execute("DELETE FROM write_probe")
            return True
        except sqlite3.Error:
            return False

    def readable(self) -> bool:
        try:
            with self.connection() as conn:
                conn.execute("SELECT 1 FROM conversion_job LIMIT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # --- batches ----------------------------------------------------------

    def create_batch(self, document_count: int, submitter_note: str | None = None) -> str:
        batch_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO batch (id, created_at, document_count, submitter_note)"
                " VALUES (?, ?, ?, ?)",
                (batch_id, now_iso(), document_count, submitter_note),
            )
        return batch_id

    def set_batch_document_count(self, batch_id: str, count: int) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE batch SET document_count = ? WHERE id = ?", (count, batch_id))

    # --- source documents -------------------------------------------------

    def upsert_source_document(
        self,
        *,
        content_hash: str,
        original_filename: str,
        size_bytes: int,
        inbox_path: str | None,
        page_count: int | None = None,
    ) -> SourceDocument:
        """Converge identical bytes on one document, keeping the first filename seen."""
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO source_document"
                " (content_hash, original_filename, size_bytes, page_count, first_seen_at,"
                "  inbox_path)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(content_hash) DO UPDATE SET"
                "   inbox_path = COALESCE(excluded.inbox_path, source_document.inbox_path),"
                "   page_count = COALESCE(excluded.page_count, source_document.page_count)",
                (content_hash, original_filename, size_bytes, page_count, now_iso(), inbox_path),
            )
        document = self.get_source_document(content_hash)
        assert document is not None
        return document

    def get_source_document(self, content_hash: str) -> SourceDocument | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM source_document WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return SourceDocument(**dict(row)) if row else None

    def set_page_count(self, content_hash: str, page_count: int | None) -> None:
        if page_count is None:
            return
        with self.connection() as conn:
            conn.execute(
                "UPDATE source_document SET page_count = ? WHERE content_hash = ?",
                (page_count, content_hash),
            )

    def clear_inbox_path(self, content_hash: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE source_document SET inbox_path = NULL WHERE content_hash = ?",
                (content_hash,),
            )

    def documents_with_inbox_file(self) -> list[SourceDocument]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM source_document WHERE inbox_path IS NOT NULL"
            ).fetchall()
        return [SourceDocument(**dict(row)) for row in rows]

    # --- jobs -------------------------------------------------------------

    def create_job(
        self,
        *,
        content_hash: str,
        submitted_filename: str,
        batch_id: str | None = None,
        status: JobStatus = JobStatus.QUEUED,
        attempt: int = 1,
        output_filename: str | None = None,
    ) -> ConversionJob:
        job_id = str(uuid.uuid4())
        stamp = now_iso()
        ended_at = stamp if status != JobStatus.QUEUED else None
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO conversion_job"
                " (id, batch_id, content_hash, submitted_filename, status, created_at,"
                "  updated_at, ended_at, attempt, output_filename)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    batch_id,
                    content_hash,
                    submitted_filename,
                    status.value,
                    stamp,
                    stamp,
                    ended_at,
                    attempt,
                    output_filename,
                ),
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> ConversionJob | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM conversion_job WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(
        self,
        *,
        limit: int = 100,
        batch_id: str | None = None,
        statuses: Iterable[str] | None = None,
        since: str | None = None,
    ) -> list[ConversionJob]:
        clauses: list[str] = []
        params: list[Any] = []
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        statuses = list(statuses or [])
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if since:
            clauses.append("updated_at > ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM conversion_job {where} ORDER BY created_at DESC, id LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def job_views(
        self,
        *,
        limit: int = 100,
        batch_id: str | None = None,
        statuses: Iterable[str] | None = None,
        since: str | None = None,
        content_hash: str | None = None,
    ) -> list[JobView]:
        """Jobs with the document and output fields the page shows, in one query.

        The page polls this every couple of seconds from several clients, so it must
        not fan out into a query per row (FR-010, SC-011).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if batch_id:
            clauses.append("j.batch_id = ?")
            params.append(batch_id)
        statuses = list(statuses or [])
        if statuses:
            clauses.append(f"j.status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if since:
            clauses.append("j.updated_at > ?")
            params.append(since)
        if content_hash:
            # Every conversion of one document, so a delete confirmation can say how many
            # entries will disappear. Not on the polling path (feature 002, R5).
            clauses.append("j.content_hash = ?")
            params.append(content_hash)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(
                f"{_JOB_VIEW_SELECT} {where} ORDER BY j.created_at DESC, j.id LIMIT ?", params
            ).fetchall()
        return [_row_to_view(row) for row in rows]

    def job_view(self, job_id: str) -> JobView | None:
        with self.connection() as conn:
            row = conn.execute(f"{_JOB_VIEW_SELECT} WHERE j.id = ?", (job_id,)).fetchone()
        return _row_to_view(row) if row else None

    def jobs_for_hash(self, content_hash: str) -> list[ConversionJob]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM conversion_job WHERE content_hash = ? ORDER BY created_at DESC",
                (content_hash,),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def next_queued_jobs(self, limit: int) -> list[ConversionJob]:
        if limit <= 0:
            return []
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM conversion_job WHERE status = 'queued'"
                " ORDER BY created_at, id LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def active_jobs(self) -> list[ConversionJob]:
        """Jobs the engine is currently working on, oldest first."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM conversion_job WHERE status IN ('submitted','running')"
                " ORDER BY created_at, id"
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def in_flight_jobs(self) -> list[ConversionJob]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM conversion_job WHERE status IN ('queued','submitted','running')"
                " ORDER BY created_at, id"
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def count_active(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM conversion_job WHERE status IN ('submitted','running')"
            ).fetchone()
        return int(row["n"])

    def backlog(self) -> Backlog:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM conversion_job"
                " WHERE status IN ('queued','submitted','running') GROUP BY status"
            ).fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        return Backlog(
            queued=counts.get(JobStatus.QUEUED.value, 0),
            converting=sum(counts.get(status.value, 0) for status in CONVERTING_STATUSES),
        )

    def batch_progress(self, batch_id: str) -> dict[str, int]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM conversion_job WHERE batch_id = ?"
                " GROUP BY status",
                (batch_id,),
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    # --- job transitions --------------------------------------------------

    def _update_job(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE conversion_job SET {assignments} WHERE id = ?",
                [*fields.values(), job_id],
            )

    def mark_submitted(self, job_id: str, engine_task_id: str, queue_position: int | None) -> None:
        self._update_job(
            job_id,
            status=JobStatus.SUBMITTED.value,
            engine_task_id=engine_task_id,
            queue_position=queue_position,
        )

    def mark_running(self, job_id: str, queue_position: int | None = None) -> None:
        self._update_job(
            job_id,
            status=JobStatus.RUNNING.value,
            started_at=now_iso(),
            queue_position=queue_position,
        )

    def set_queue_position(self, job_id: str, queue_position: int | None) -> None:
        self._update_job(job_id, queue_position=queue_position)

    def requeue(self, job_id: str, attempt: int) -> None:
        """Restart recovery: back to `queued` for resubmission (never poll a stale task id)."""
        self._update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            attempt=attempt,
            engine_task_id=None,
            queue_position=None,
            started_at=None,
        )

    def finish_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        failure_reason: str | None = None,
        engine_errors: list[str] | None = None,
        output_filename: str | None = None,
    ) -> None:
        self._update_job(
            job_id,
            status=status.value,
            ended_at=now_iso(),
            failure_reason=failure_reason,
            engine_errors=json.dumps(engine_errors) if engine_errors else None,
            output_filename=output_filename,
        )

    def record_outputs_and_finish(
        self,
        *,
        job_id: str,
        content_hash: str,
        outputs: list[tuple[str, int, int | None, str | None]],
        engine_status: str,
        status: JobStatus,
        engine_errors: list[str] | None = None,
        page_count: int | None = None,
        missing_page_ranges: list[tuple[int, int]] | None = None,
        superseded: list[str] | None = None,
    ) -> None:
        """Persist every output row and the terminal job state in one transaction.

        A document above the section threshold writes several files (FR-033); an ordinary
        one writes a single file and is the same code path with a list of one.
        `superseded` names rows this document wrote on a previous run that the new set
        does not replace — their files are deleted by the caller (research.md R13).
        """
        stamp = now_iso()
        primary = outputs[0][0] if outputs else None
        with self.connection() as conn:
            for name in superseded or []:
                conn.execute("DELETE FROM markdown_output WHERE output_filename = ?", (name,))
            for name, size_bytes, ordinal, title in outputs:
                conn.execute(
                    "INSERT INTO markdown_output"
                    " (output_filename, content_hash, job_id, bytes, written_at,"
                    "  engine_status, section_ordinal, section_title)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(output_filename) DO UPDATE SET"
                    "   job_id = excluded.job_id, bytes = excluded.bytes,"
                    "   written_at = excluded.written_at,"
                    "   engine_status = excluded.engine_status,"
                    "   section_ordinal = excluded.section_ordinal,"
                    "   section_title = excluded.section_title",
                    (name, content_hash, job_id, size_bytes, stamp, engine_status, ordinal, title),
                )
            if page_count is not None:
                conn.execute(
                    "UPDATE source_document SET page_count = ? WHERE content_hash = ?",
                    (page_count, content_hash),
                )
            conn.execute(
                "UPDATE conversion_job SET status = ?, ended_at = ?, updated_at = ?,"
                " output_filename = ?, engine_errors = ?, missing_page_ranges = ?"
                " WHERE id = ?",
                (
                    status.value,
                    stamp,
                    stamp,
                    primary,
                    json.dumps(engine_errors) if engine_errors else None,
                    json.dumps(missing_page_ranges) if missing_page_ranges else None,
                    job_id,
                ),
            )

    def outputs_for_hash(self, content_hash: str) -> list[MarkdownOutput]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM markdown_output WHERE content_hash = ?"
                " ORDER BY COALESCE(section_ordinal, 0), output_filename",
                (content_hash,),
            ).fetchall()
        return [MarkdownOutput(**dict(row)) for row in rows]

    def record_output_and_finish(
        self,
        *,
        job_id: str,
        content_hash: str,
        output_filename: str,
        size_bytes: int,
        engine_status: str,
        status: JobStatus,
        engine_errors: list[str] | None = None,
        page_count: int | None = None,
    ) -> None:
        """Persist the output row and the terminal job state in one transaction.

        The engine serves each result exactly once (research.md R3), so the write to
        the outbox and this commit are the only chances to keep it.
        """
        stamp = now_iso()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO markdown_output"
                " (output_filename, content_hash, job_id, bytes, written_at, engine_status)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(output_filename) DO UPDATE SET"
                "   job_id = excluded.job_id, bytes = excluded.bytes,"
                "   written_at = excluded.written_at, engine_status = excluded.engine_status",
                (output_filename, content_hash, job_id, size_bytes, stamp, engine_status),
            )
            if page_count is not None:
                conn.execute(
                    "UPDATE source_document SET page_count = ? WHERE content_hash = ?",
                    (page_count, content_hash),
                )
            conn.execute(
                "UPDATE conversion_job SET status = ?, ended_at = ?, updated_at = ?,"
                " output_filename = ?, engine_errors = ? WHERE id = ?",
                (
                    status.value,
                    stamp,
                    stamp,
                    output_filename,
                    json.dumps(engine_errors) if engine_errors else None,
                    job_id,
                ),
            )

    # --- parts (FR-034) ---------------------------------------------------

    def create_parts(self, job_id: str, ranges: list[tuple[int, int]]) -> list[ConversionPart]:
        """Record one part per page range and set the job's part count in one step."""
        stamp = now_iso()
        with self.connection() as conn:
            for ordinal, (first_page, last_page) in enumerate(ranges, start=1):
                conn.execute(
                    "INSERT INTO conversion_part"
                    " (id, job_id, ordinal, first_page, last_page, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), job_id, ordinal, first_page, last_page, "queued", stamp),
                )
            conn.execute(
                "UPDATE conversion_job SET part_count = ?, updated_at = ? WHERE id = ?",
                (len(ranges), stamp, job_id),
            )
        return self.part_states_for_job(job_id)

    # Reading order is by first page, not by ordinal: halving a part that ran out of time
    # appends its two halves with fresh ordinals, so ordinal is a unique name for a part
    # and its scratch file, not a position in the document (FR-038).
    _PARTS_IN_READING_ORDER = " FROM conversion_part WHERE job_id = ? ORDER BY first_page, ordinal"

    def parts_for_job(self, job_id: str) -> list[ConversionPart]:
        """Every part of a job, in reading order, **including its converted Markdown**.

        Only the join needs this. Every other caller wants `part_states_for_job`: a part's
        Markdown is a slice of the finished document, and reading all of them costs the
        whole document in memory.
        """
        with self.connection() as conn:
            rows = conn.execute("SELECT *" + self._PARTS_IN_READING_ORDER, (job_id,)).fetchall()
        return [ConversionPart(**dict(row)) for row in rows]

    def part_states_for_job(self, job_id: str) -> list[ConversionPart]:
        """The same parts without their Markdown, for the callers that only need status.

        The dispatcher asks what its parts are doing on every pass, a few seconds apart.
        Answering with `SELECT *` meant reading the entire converted document out of the
        database two or three times a pass and holding it — inside a 512 MB container, on
        a document of two thousand pages, that is the service being killed and restarted.

        `markdown` is None on these rows. That is why they are a separate call and not a
        flag: a part with no Markdown looks exactly like a part that failed, and handing
        one of these to the join would write a gap where a converted page belongs.
        """
        columns = (
            "id, job_id, ordinal, first_page, last_page, part_path, status, engine_task_id,"
            " NULL AS markdown, failure_reason, created_at, started_at, ended_at, attempt,"
            " split_depth"
        )
        with self.connection() as conn:
            rows = conn.execute(f"SELECT {columns}{self._PARTS_IN_READING_ORDER}", (job_id,))
            return [ConversionPart(**dict(row)) for row in rows.fetchall()]

    def set_part_path(self, part_id: str, part_path: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE conversion_part SET part_path = ? WHERE id = ?", (part_path, part_id)
            )

    def mark_part_submitted(self, part_id: str, task_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE conversion_part SET status = 'submitted', engine_task_id = ?,"
                " started_at = COALESCE(started_at, ?) WHERE id = ?",
                (task_id, now_iso(), part_id),
            )

    def mark_part_running(self, part_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE conversion_part SET status = 'running',"
                " started_at = COALESCE(started_at, ?) WHERE id = ?",
                (now_iso(), part_id),
            )

    def finish_part(
        self,
        part_id: str,
        status: PartStatus,
        *,
        markdown: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        """Commit a part's outcome, and its Markdown, in one transaction.

        For a succeeded part this is the only chance to keep the conversion: the engine
        serves each result exactly once, so the fetch and this write have to be one step
        (research.md R3).
        """
        stamp = now_iso()
        with self.connection() as conn:
            conn.execute(
                "UPDATE conversion_part SET status = ?, markdown = ?, failure_reason = ?,"
                " ended_at = ? WHERE id = ?",
                (status.value, markdown, failure_reason, stamp, part_id),
            )
            self._recount_parts(conn, part_id=part_id)

    def requeue_unfinished_parts(self, job_id: str) -> int:
        """Return unfinished parts to the queue after a restart, keeping finished ones.

        Engine task ids do not survive an engine restart, so a part that was in flight is
        resubmitted. A part that already succeeded keeps its Markdown and is not converted
        again — that work is done and paid for (data-model.md restart rules).
        """
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE conversion_part SET status = 'queued', engine_task_id = NULL,"
                " started_at = NULL WHERE job_id = ? AND status IN ('submitted','running')",
                (job_id,),
            )
            reset = cursor.rowcount
            self._recount_parts(conn, job_id=job_id)
        return reset

    def requeue_part(self, part_id: str) -> None:
        """Put one part back in the queue for another attempt (FR-038)."""
        with self.connection() as conn:
            conn.execute(
                "UPDATE conversion_part SET status = 'queued', engine_task_id = NULL,"
                " started_at = NULL, failure_reason = NULL, attempt = attempt + 1"
                " WHERE id = ?",
                (part_id,),
            )
            self._recount_parts(conn, part_id=part_id)

    def split_part(self, part_id: str, ranges: list[tuple[int, int]]) -> list[ConversionPart]:
        """Replace one part with smaller ones covering the same pages (FR-038).

        The replacements take new ordinals at the end — `parts_for_job` reads in page
        order, so where they sit in the table does not matter — and a deeper split depth,
        which is what stops a range that will never convert from being halved forever.
        """
        stamp = now_iso()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT job_id, split_depth FROM conversion_part WHERE id = ?", (part_id,)
            ).fetchone()
            if row is None:
                return []
            job_id = row["job_id"]
            next_ordinal = int(
                conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 AS n FROM conversion_part"
                    " WHERE job_id = ?",
                    (job_id,),
                ).fetchone()["n"]
            )
            conn.execute("DELETE FROM conversion_part WHERE id = ?", (part_id,))
            for offset, (first_page, last_page) in enumerate(ranges):
                conn.execute(
                    "INSERT INTO conversion_part"
                    " (id, job_id, ordinal, first_page, last_page, status, created_at,"
                    "  split_depth)"
                    " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (
                        str(uuid.uuid4()),
                        job_id,
                        next_ordinal + offset,
                        first_page,
                        last_page,
                        stamp,
                        int(row["split_depth"]) + 1,
                    ),
                )
            self._recount_parts(conn, job_id=job_id)
        return self.part_states_for_job(job_id)

    @staticmethod
    def _recount_parts(
        conn: sqlite3.Connection, *, job_id: str | None = None, part_id: str | None = None
    ) -> None:
        """Re-derive the job's part counters from the parts themselves.

        Both counters are caches of a COUNT — the page shows *part 7 of 20* from them —
        and both move when a part is requeued or halved, not only when one finishes.
        """
        if job_id is None:
            row = conn.execute(
                "SELECT job_id FROM conversion_part WHERE id = ?", (part_id,)
            ).fetchone()
            if row is None:
                return
            job_id = str(row["job_id"])
        conn.execute(
            "UPDATE conversion_job SET"
            "   part_count = (SELECT COUNT(*) FROM conversion_part WHERE job_id = ?),"
            "   parts_completed = (SELECT COUNT(*) FROM conversion_part WHERE job_id = ?"
            "                      AND status IN ('succeeded','failed','timed_out')),"
            "   updated_at = ?"
            " WHERE id = ?",
            (job_id, job_id, now_iso(), job_id),
        )

    def count_parts_in_flight(self, job_id: str) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM conversion_part WHERE job_id = ?"
                " AND status IN ('submitted','running')",
                (job_id,),
            ).fetchone()
        return int(row["n"])

    # --- outputs ----------------------------------------------------------

    def get_output(self, output_filename: str) -> MarkdownOutput | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM markdown_output WHERE output_filename = ?", (output_filename,)
            ).fetchone()
        return MarkdownOutput(**dict(row)) if row else None

    def get_output_for_hash(self, content_hash: str) -> MarkdownOutput | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM markdown_output WHERE content_hash = ?"
                " ORDER BY written_at DESC LIMIT 1",
                (content_hash,),
            ).fetchone()
        return MarkdownOutput(**dict(row)) if row else None

    def outbox_inventory(self, limit: int = 500) -> list[MarkdownOutput]:
        """The operator's import checklist (data-model.md derived views)."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM markdown_output ORDER BY written_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [MarkdownOutput(**dict(row)) for row in rows]

    def outbox_document_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM markdown_output").fetchone()
        return int(row["n"])

    # --- deletion (feature 002) -------------------------------------------

    def delete_document_rows(self, content_hash: str) -> list[str]:
        """Remove every row belonging to one document. Returns the job ids removed.

        The order is forced by the foreign keys, which are enforced on every connection:
        `conversion_job.output_filename` references `markdown_output`, so the jobs go
        first, and `markdown_output.content_hash` references `source_document`, so the
        outputs go before the document. `conversion_part.job_id` is ON DELETE CASCADE, so
        the parts need no statement of their own.

        `batch` rows are deliberately left alone. `conversion_job.batch_id` is ON DELETE
        SET NULL, a batch that loses all its jobs is harmless, and deleting one would take
        other documents' jobs with it (data-model.md).

        The caller unlinks the files *before* calling this. A crash between the two leaves
        rows pointing at absent files, which every read path already survives; the reverse
        would leave files in the outbox that no record mentions (research.md R7).
        """
        with self.connection() as conn:
            job_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM conversion_job WHERE content_hash = ?", (content_hash,)
                ).fetchall()
            ]
            conn.execute("DELETE FROM conversion_job WHERE content_hash = ?", (content_hash,))
            conn.execute("DELETE FROM markdown_output WHERE content_hash = ?", (content_hash,))
            conn.execute("DELETE FROM source_document WHERE content_hash = ?", (content_hash,))
        return job_ids

    def all_content_hashes(self) -> list[str]:
        """Every document the registry knows about, newest first (feature 002, FR-027)."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT content_hash FROM source_document ORDER BY first_seen_at DESC"
            ).fetchall()
        return [row["content_hash"] for row in rows]

    # --- retention --------------------------------------------------------

    def prune_history(self, before: str) -> int:
        """Delete terminal jobs older than `before`.

        `markdown_output` rows and outbox files are never touched — the outbox is the
        durable record (FR-013).
        """
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM conversion_job WHERE ended_at IS NOT NULL AND ended_at < ?"
                " AND status NOT IN ('queued','submitted','running')",
                (before,),
            )
            return cursor.rowcount


@dataclass
class JobView:
    """A job plus the document and output fields the API returns alongside it."""

    job: ConversionJob
    size_bytes: int
    page_count: int | None
    original_filename: str
    output_bytes: int | None
    engine_status: str | None


_JOB_VIEW_SELECT = """
    SELECT j.*, d.size_bytes AS doc_size_bytes, d.page_count AS doc_page_count,
           d.original_filename AS doc_original_filename,
           o.bytes AS output_bytes, o.engine_status AS output_engine_status
      FROM conversion_job j
      JOIN source_document d ON d.content_hash = j.content_hash
      LEFT JOIN markdown_output o ON o.output_filename = j.output_filename
"""


def _row_to_view(row: sqlite3.Row) -> JobView:
    data = dict(row)
    return JobView(
        job=_row_to_job_data(data),
        size_bytes=data["doc_size_bytes"],
        page_count=data["doc_page_count"],
        original_filename=data["doc_original_filename"],
        output_bytes=data["output_bytes"],
        engine_status=data["output_engine_status"],
    )


def _row_to_job_data(data: dict[str, Any]) -> ConversionJob:
    fields = {name: data[name] for name in ConversionJob.model_fields if name in data}
    errors = fields.get("engine_errors")
    fields["engine_errors"] = json.loads(errors) if errors else None
    ranges = fields.get("missing_page_ranges")
    fields["missing_page_ranges"] = json.loads(ranges) if ranges else None
    return ConversionJob(**fields)


def _row_to_job(row: sqlite3.Row) -> ConversionJob:
    data = dict(row)
    errors = data.get("engine_errors")
    data["engine_errors"] = json.loads(errors) if errors else None
    ranges = data.get("missing_page_ranges")
    data["missing_page_ranges"] = json.loads(ranges) if ranges else None
    return ConversionJob(**data)


__all__ = ["IN_FLIGHT_STATUSES", "SCHEMA", "Database", "JobView"]
