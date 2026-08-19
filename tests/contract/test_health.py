"""`GET /api/health` and `GET /healthz` (FR-018)."""

import pytest

from tests.conftest import pdf_bytes

pytestmark = pytest.mark.contract


async def test_health_payload_shape(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "engine", "backlog", "outbox", "database", "version"}
    assert body["status"] == "ok"
    assert body["engine"]["reachable"] is True
    assert body["engine"]["checked_at"].endswith("Z")
    assert set(body["outbox"]) == {"writable", "free_bytes", "documents"}
    assert body["outbox"]["writable"] is True
    assert body["database"]["writable"] is True
    assert body["version"]


async def test_health_is_503_and_degraded_when_the_engine_is_unreachable(client, stub_engine):
    stub_engine.reachable = False
    response = await client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["engine"]["reachable"] is False
    assert set(body) == {"status", "engine", "backlog", "outbox", "database", "version"}


async def test_uploads_are_still_accepted_while_degraded(client, stub_engine, upload):
    stub_engine.reachable = False
    response = await upload(("report.pdf", pdf_bytes(b"a")))
    assert response.status_code == 202
    assert response.json()["accepted"][0]["status"] == "queued"
    assert (await client.get("/api/health")).json()["backlog"]["queued"] == 1


async def test_backlog_and_outbox_counts_are_reported(convert, client):
    await convert(("report.pdf", pdf_bytes(b"a")))
    body = (await client.get("/api/health")).json()
    assert body["backlog"] == {"queued": 0, "converting": 0}
    assert body["outbox"]["documents"] == 1
    assert body["outbox"]["free_bytes"] > 0


async def test_healthz_is_cheap_and_makes_no_engine_call(client, stub_engine):
    stub_engine.reachable = False
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
