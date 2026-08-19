"""Uploaded PDFs are reaped on the right clock, and never while work is live."""

import pytest

from pdf2md.clock import iso_ago
from pdf2md.db import Database
from pdf2md.models import JobStatus
from pdf2md.storage import Storage, reap_inbox

pytestmark = pytest.mark.unit

RETENTION_HOURS = 48
FAILED_RETENTION_DAYS = 14


@pytest.fixture
def parts(tmp_path):
    db = Database(tmp_path / "db" / "pdf2md.sqlite")
    db.migrate()
    storage = Storage(tmp_path / "inbox", tmp_path / "outbox")
    storage.ensure_directories()
    return db, storage


def _document(db, storage, content_hash, filename="report.pdf"):
    storage.inbox_file(content_hash).write_bytes(b"%PDF-1.7 body")
    db.upsert_source_document(
        content_hash=content_hash,
        original_filename=filename,
        size_bytes=13,
        inbox_path=str(storage.inbox_file(content_hash)),
    )


def _finish(db, job_id, status, ended_at):
    db.finish_job(job_id, status, failure_reason="reason" if status is JobStatus.FAILED else None)
    with db.connection() as conn:
        conn.execute("UPDATE conversion_job SET ended_at = ? WHERE id = ?", (ended_at, job_id))


def _reap(db, storage):
    return reap_inbox(
        db,
        storage,
        retention_hours=RETENTION_HOURS,
        failed_retention_days=FAILED_RETENTION_DAYS,
    )


def test_a_succeeded_upload_is_reaped_on_the_short_clock(parts):
    db, storage = parts
    content_hash = "a" * 64
    _document(db, storage, content_hash)
    job = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, job.id, JobStatus.SUCCEEDED, iso_ago(hours=49))

    assert _reap(db, storage) == [content_hash]
    assert not storage.has_inbox_file(content_hash)
    assert db.get_source_document(content_hash).inbox_path is None


def test_a_succeeded_upload_inside_its_window_survives(parts):
    db, storage = parts
    content_hash = "b" * 64
    _document(db, storage, content_hash)
    job = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, job.id, JobStatus.SUCCEEDED, iso_ago(hours=47))

    assert _reap(db, storage) == []
    assert storage.has_inbox_file(content_hash)


def test_a_failed_upload_survives_the_short_clock_and_is_reaped_on_the_long_one(parts):
    db, storage = parts
    content_hash = "c" * 64
    _document(db, storage, content_hash)
    job = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, job.id, JobStatus.FAILED, iso_ago(hours=72))

    assert _reap(db, storage) == []
    assert storage.has_inbox_file(content_hash)

    _finish(db, job.id, JobStatus.FAILED, iso_ago(days=15))
    assert _reap(db, storage) == [content_hash]
    assert not storage.has_inbox_file(content_hash)


def test_a_timed_out_upload_uses_the_long_clock_too(parts):
    db, storage = parts
    content_hash = "d" * 64
    _document(db, storage, content_hash)
    job = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, job.id, JobStatus.TIMED_OUT, iso_ago(days=13))

    assert _reap(db, storage) == []
    _finish(db, job.id, JobStatus.TIMED_OUT, iso_ago(days=15))
    assert _reap(db, storage) == [content_hash]


def test_a_document_with_a_live_job_is_never_reaped(parts):
    db, storage = parts
    content_hash = "e" * 64
    _document(db, storage, content_hash)
    old = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, old.id, JobStatus.SUCCEEDED, iso_ago(days=30))
    db.create_job(content_hash=content_hash, submitted_filename="report.pdf")  # queued again

    assert _reap(db, storage) == []
    assert storage.has_inbox_file(content_hash)


def test_a_document_that_succeeded_once_uses_the_short_clock_despite_earlier_failures(parts):
    db, storage = parts
    content_hash = "f" * 64
    _document(db, storage, content_hash)
    failed = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, failed.id, JobStatus.FAILED, iso_ago(days=3))
    succeeded = db.create_job(content_hash=content_hash, submitted_filename="report.pdf")
    _finish(db, succeeded.id, JobStatus.SUCCEEDED, iso_ago(hours=49))

    assert _reap(db, storage) == [content_hash]
