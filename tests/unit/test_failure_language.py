"""Every failure a user sees must read like a sentence, not a stack trace (FR-011)."""

import re

import pytest

from pdf2md import dispatcher as dispatcher_module
from pdf2md.docling_client import _FAILURE_PATTERNS, _GENERIC_FAILURE, DoclingClient

pytestmark = pytest.mark.unit

USER_FACING_MESSAGES = [message for _, message in _FAILURE_PATTERNS] + [
    _GENERIC_FAILURE,
    dispatcher_module.RESTART_LOST_FILE_REASON,
    dispatcher_module.MISSING_UPLOAD_REASON,
    dispatcher_module.LOST_RESULT_REASON,
    dispatcher_module.TIMEOUT_REASON_TEMPLATE.format(minutes=45),
]

JARGON = [
    "traceback",
    "exception",
    "runtimeerror",
    "stderr",
    "null",
    "none",
    "http ",
    "status code",
    "task_id",
    "engine_status",
    "sqlite",
    "asyncio",
    "docling",
    "pdfium",
    "errno",
]


@pytest.mark.parametrize("message", USER_FACING_MESSAGES)
def test_messages_are_plain_sentences(message):
    assert message[0].isupper(), message
    assert message.endswith((".", "!")), message
    assert 20 < len(message) < 220, message
    assert "\n" not in message


@pytest.mark.parametrize("message", USER_FACING_MESSAGES)
def test_messages_contain_no_engine_jargon(message):
    lowered = message.lower()
    for term in JARGON:
        assert term not in lowered, f"{message!r} contains {term!r}"
    assert not re.search(r"[{}<>]|\bself\b|::", message), message


@pytest.mark.parametrize("message", USER_FACING_MESSAGES)
def test_messages_tell_the_reader_what_to_do_or_what_happened(message):
    # Not a style rule for its own sake: a failure the reader cannot act on is a
    # support call for the operator.
    assert any(
        hint in message.lower()
        for hint in ("try", "upload", "split", "remove", "convert it again", "stopped", "later")
    ), message


@pytest.mark.parametrize(
    ("errors", "expected_fragment"),
    [
        (["Traceback (most recent call last): PdfiumError code 4"], "damaged or incomplete"),
        (["File is encrypted and requires a password"], "password-protected"),
        (["Conversion timed out after 2400s"], "took too long"),
        (["Document has too many pages (3000 > 2000)"], "larger than the converter accepts"),
        (["MemoryError: worker exhausted"], "ran out of resources"),
        (["unsupported format detected"], "could not handle"),
        (["Input document contract.pdf is not valid."], "would not accept this document"),
        (["something nobody anticipated"], "could not be converted"),
    ],
)
def test_engine_detail_is_translated_not_echoed(errors, expected_fragment):
    reason = DoclingClient.failure_reason_from(engine_status="failure", errors=errors)
    assert expected_fragment in reason
    assert errors[0] not in reason


def test_the_raw_engine_detail_is_kept_out_of_the_user_message_but_not_lost():
    """`engine_errors` keeps the detail for the log and the detail view."""
    from pdf2md.models import JobDetail

    assert "engine_errors" in JobDetail.model_fields
