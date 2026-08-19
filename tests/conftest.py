"""Shared fixtures: temp volumes, an app wired to the stub engine, an ASGI client."""

from __future__ import annotations

import io

import httpx
import pytest
from pypdf import PdfWriter

from pdf2md.config import Settings
from pdf2md.main import create_app
from tests.stubs.docling_stub import StubEngine

MINIMAL_PDF_TAIL = b"1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\nstartxref\n0\n%%EOF\n"


def pdf_bytes(marker: bytes = b"body", *, encrypted: bool = False, pages: int = 1) -> bytes:
    """A real PDF of `pages` blank pages; `marker` makes the content unique.

    These have to be genuinely parseable now: the service reads the page tree at upload to
    decide whole/split/refused (FR-036), so a byte string that merely looks like a PDF
    would be refused as damaged before any test got to its actual subject.
    """
    writer = PdfWriter()
    for _ in range(max(1, pages)):
        writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Keywords": marker.decode("latin-1")})
    if encrypted:
        writer.encrypt("password")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def damaged_pdf_bytes(marker: bytes = b"broken") -> bytes:
    """Passes a magic-byte sniff, fails a structural read — the FR-007 rejection path."""
    return b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + marker + b"\n" + MINIMAL_PDF_TAIL


@pytest.fixture
def stub_engine() -> StubEngine:
    return StubEngine(api_key="test-key")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        engine_url="http://engine.test",
        engine_api_key="test-key",
        db_path=tmp_path / "db" / "pdf2md.sqlite",
        inbox_path=tmp_path / "inbox",
        outbox_path=tmp_path / "outbox",
        poll_interval_seconds=0.001,
        dispatcher_enabled=False,
        require_private_engine_url=False,
    )


@pytest.fixture
async def app(settings, stub_engine):
    application = create_app(
        settings=settings, engine_transport=httpx.ASGITransport(app=stub_engine.app)
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def dispatcher(app):
    return app.state.dispatcher


@pytest.fixture
def db(app):
    return app.state.db


@pytest.fixture
def storage(app):
    return app.state.storage


@pytest.fixture
def upload(client):
    """Post one or more (filename, bytes) pairs to /api/uploads."""

    async def _upload(*documents: tuple[str, bytes], note: str | None = None):
        files = [("files", (name, payload, "application/pdf")) for name, payload in documents]
        data = {"note": note} if note is not None else None
        return await client.post("/api/uploads", files=files, data=data)

    return _upload


@pytest.fixture
def convert(upload, dispatcher):
    """Upload documents and run the dispatcher until nothing is in flight."""

    async def _convert(*documents: tuple[str, bytes], note: str | None = None):
        response = await upload(*documents, note=note)
        await dispatcher.drain()
        return response

    return _convert
