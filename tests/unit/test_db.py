"""Schema, migrations, and the queries the page and dispatcher depend on."""

import sqlite3

import pytest

from pdf2md.clock import iso_ago
from pdf2md.db import SCHEMA, Database
from pdf2md.models import JobStatus

pytestmark = pytest.mark.unit

HASH = "a" * 64
OTHER_HASH = "b" * 64


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "db" / "pdf2md.sqlite")
    database.migrate()
    return database


def _document(db, content_hash=HASH, filename="report.pdf"):
    return db.upsert_source_document(
        content_hash=content_hash,
        original_filename=filename,
        size_bytes=1234,
        inbox_path=f"/data/inbox/{content_hash}.pdf",
    )


def test_migrations_are_idempotent(db):
    db.migrate()
    db.migrate()
    with db.connection() as conn:
        names = [row["name"] for row in conn.execute("SELECT name FROM schema_migration")]
    assert names == [name for name, _ in SCHEMA]


def test_pragmas_are_set_on_every_connection(db):
    with db.connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_status_check_constraint_rejects_an_unknown_state(db):
    _document(db)
    job = db.create_job(content_hash=HASH, submitted_filename="report.pdf")
    with pytest.raises(sqlite3.IntegrityError), db.connection() as conn:
        conn.execute("UPDATE conversion_job SET status = 'wat' WHERE id = ?", (job.id,))


def test_identical_bytes_converge_on_one_document_keeping_the_first_filename(db):
    _document(db, filename="first.pdf")
    again = db.upsert_source_document(
        content_hash=HASH, original_filename="second.pdf", size_bytes=1234, inbox_path=None
    )
    assert again.original_filename == "first.pdf"
    assert again.inbox_path == f"/data/inbox/{HASH}.pdf"


def test_backlog_counts_queued_and_converting(db):
    _document(db)
    queued = db.create_job(content_hash=HASH, submitted_filename="a.pdf")
    running = db.create_job(content_hash=HASH, submitted_filename="b.pdf")
    db.mark_submitted(running.id, "task", 1)
    db.mark_running(running.id)
    backlog = db.backlog()
    assert (backlog.queued, backlog.converting) == (1, 1)
    assert [job.id for job in db.next_queued_jobs(10)] == [queued.id]
    assert db.count_active() == 1


def test_since_filter_returns_only_changed_jobs(db):
    _document(db)
    first = db.create_job(content_hash=HASH, submitted_filename="a.pdf")
    marker = db.get_job(first.id).updated_at
    second = db.create_job(content_hash=HASH, submitted_filename="b.pdf")
    changed = db.list_jobs(since=marker)
    assert [job.id for job in changed] == [second.id]


def test_output_and_terminal_state_commit_together(db):
    _document(db)
    job = db.create_job(content_hash=HASH, submitted_filename="report.pdf")
    db.record_output_and_finish(
        job_id=job.id,
        content_hash=HASH,
        output_filename="report--aaaaaaaaaaaa.md",
        size_bytes=42,
        engine_status="partial_success",
        status=JobStatus.SUCCEEDED,
        engine_errors=["Page 14: table structure could not be resolved"],
        page_count=12,
    )
    stored = db.get_job(job.id)
    assert stored.status is JobStatus.SUCCEEDED
    assert stored.output_filename == "report--aaaaaaaaaaaa.md"
    assert stored.engine_errors == ["Page 14: table structure could not be resolved"]
    assert stored.ended_at is not None
    output = db.get_output_for_hash(HASH)
    assert output.engine_status == "partial_success" and output.bytes == 42
    assert db.get_source_document(HASH).page_count == 12
    assert db.outbox_document_count() == 1


def test_reconverting_the_same_document_replaces_its_output_row(db):
    _document(db)
    for _ in range(2):
        job = db.create_job(content_hash=HASH, submitted_filename="report.pdf")
        db.record_output_and_finish(
            job_id=job.id,
            content_hash=HASH,
            output_filename="report--aaaaaaaaaaaa.md",
            size_bytes=42,
            engine_status="success",
            status=JobStatus.SUCCEEDED,
        )
    assert db.outbox_document_count() == 1


def test_pruning_history_never_deletes_outputs(db):
    _document(db)
    job = db.create_job(content_hash=HASH, submitted_filename="report.pdf")
    db.record_output_and_finish(
        job_id=job.id,
        content_hash=HASH,
        output_filename="report--aaaaaaaaaaaa.md",
        size_bytes=42,
        engine_status="success",
        status=JobStatus.SUCCEEDED,
    )
    assert db.prune_history(before=iso_ago(days=30)) == 0
    with db.connection() as conn:
        conn.execute(
            "UPDATE conversion_job SET ended_at = ? WHERE id = ?", (iso_ago(days=31), job.id)
        )
    assert db.prune_history(before=iso_ago(days=30)) == 1
    assert db.get_job(job.id) is None
    assert db.outbox_document_count() == 1
    assert db.get_output("report--aaaaaaaaaaaa.md") is not None


def test_pruning_leaves_in_flight_jobs_alone(db):
    _document(db)
    job = db.create_job(content_hash=HASH, submitted_filename="report.pdf")
    db.mark_submitted(job.id, "task", None)
    assert db.prune_history(before=iso_ago(days=0)) == 0
    assert db.get_job(job.id) is not None


def test_migrating_an_existing_database_keeps_its_job_history(tmp_path):
    """The status CHECK is rebuilt for `succeeded_incomplete`, and SQLite cannot alter a
    constraint in place. History has to survive that rebuild — it survives a redeploy
    (FR-017), so it must survive a schema change too.
    """
    from pdf2md.clock import now_iso
    from pdf2md.db import SCHEMA, Database

    database = Database(tmp_path / "old.sqlite")
    # Bring up only the original schema, as a stack deployed before splitting would have.
    with database.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.executescript(SCHEMA[0][1])
        conn.execute(
            "INSERT INTO schema_migration (name, applied_at) VALUES (?, ?)",
            (SCHEMA[0][0], now_iso()),
        )
        conn.execute(
            "INSERT INTO source_document"
            " (content_hash, original_filename, size_bytes, first_seen_at)"
            " VALUES ('abc123', 'old.pdf', 10, ?)",
            (now_iso(),),
        )
        conn.execute(
            "INSERT INTO conversion_job"
            " (id, content_hash, submitted_filename, status, created_at, updated_at, attempt)"
            " VALUES ('job-1', 'abc123', 'old.pdf', 'succeeded', ?, ?, 1)",
            (now_iso(), now_iso()),
        )

    database.migrate()

    with database.connection() as conn:
        rows = list(conn.execute("SELECT id, status, part_count FROM conversion_job"))
        assert [(r["id"], r["status"], r["part_count"]) for r in rows] == [
            ("job-1", "succeeded", 1)
        ]
        # and the new status is now accepted where it was not before
        conn.execute(
            "INSERT INTO conversion_job"
            " (id, content_hash, submitted_filename, status, created_at, updated_at, attempt)"
            " VALUES ('job-2', 'abc123', 'new.pdf', 'succeeded_incomplete', ?, ?, 1)",
            (now_iso(), now_iso()),
        )
