"""Outputs and job history survive a restart against the same volumes (FR-017)."""

import httpx
import pytest

from pdf2md.main import create_app
from tests.conftest import pdf_bytes

pytestmark = pytest.mark.integration


async def restarted_client(settings, stub_engine):
    """A second app instance over the same database, inbox, and outbox."""
    app = create_app(settings=settings, engine_transport=httpx.ASGITransport(app=stub_engine.app))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, app


async def test_history_and_outbox_survive_a_restart(convert, client, settings, stub_engine):
    body = (await convert(("report.pdf", pdf_bytes(b"a")))).json()
    job_id = body["accepted"][0]["job_id"]
    before = (await client.get(f"/api/jobs/{job_id}")).json()

    async for restarted, _ in restarted_client(settings, stub_engine):
        after = (await restarted.get(f"/api/jobs/{job_id}")).json()
        assert after["status"] == before["status"] == "succeeded"
        assert after["output_filename"] == before["output_filename"]
        download = await restarted.get(f"/api/jobs/{job_id}/markdown")
        assert download.status_code == 200
        assert download.text
        assert (await restarted.get("/api/health")).json()["outbox"]["documents"] == 1


async def test_a_document_converted_before_the_restart_is_not_converted_again(
    convert, settings, stub_engine
):
    await convert(("report.pdf", pdf_bytes(b"a")))
    submissions_before = len(stub_engine.submissions)

    async for restarted, app in restarted_client(settings, stub_engine):
        response = await restarted.post(
            "/api/uploads", files=[("files", ("report.pdf", pdf_bytes(b"a"), "application/pdf"))]
        )
        accepted = response.json()["accepted"][0]
        assert accepted["status"] == "already_converted"
        assert accepted["output_filename"]
        await app.state.dispatcher.drain()
        assert len(stub_engine.submissions) == submissions_before


async def test_the_registry_is_created_on_first_start(settings, stub_engine):
    assert not settings.db_path.exists()
    async for restarted, _ in restarted_client(settings, stub_engine):
        assert settings.db_path.exists()
        assert (await restarted.get("/api/jobs")).json()["jobs"] == []
