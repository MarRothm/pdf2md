"""`GET /api/health` — operator-facing detail, also rendered on the page (FR-018)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pdf2md import __version__
from pdf2md.api import db_of, engine_of, settings_of, storage_of
from pdf2md.clock import now_iso
from pdf2md.models import (
    DatabaseHealth,
    DispatcherHealth,
    EngineHealth,
    HealthResponse,
    OutboxHealth,
)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> JSONResponse:
    db = db_of(request)
    storage = storage_of(request)
    engine = engine_of(request)
    settings = settings_of(request)

    dispatcher = getattr(request.app.state, "dispatcher", None)
    engine_reachable = await engine.is_healthy()
    outbox_writable = storage.is_writable(storage.outbox_path)
    database_writable = db.writable()

    # A dispatcher that has stopped, or an engine refusing every submission, is a stalled
    # queue — reported as `ok`, that is a page saying the converter is ready while nothing
    # moves for hours. `dispatcher_enabled` is off in tests, which is not a fault.
    loop = DispatcherHealth(
        running=dispatcher.is_alive if dispatcher is not None else False,
        last_pass_at=dispatcher.last_pass_at if dispatcher is not None else None,
        last_engine_error=dispatcher.last_engine_error if dispatcher is not None else None,
        last_engine_error_at=dispatcher.last_engine_error_at if dispatcher is not None else None,
    )
    stalled = bool(loop.last_engine_error) or (settings.dispatcher_enabled and not loop.running)

    payload = HealthResponse(
        status="ok"
        if engine_reachable and outbox_writable and database_writable and not stalled
        else "degraded",
        engine=EngineHealth(reachable=engine_reachable, checked_at=now_iso()),
        backlog=db.backlog(),
        outbox=OutboxHealth(
            writable=outbox_writable,
            free_bytes=storage.free_bytes(storage.outbox_path),
            documents=db.outbox_document_count(),
        ),
        database=DatabaseHealth(writable=database_writable),
        version=__version__,
        dispatcher=loop,
    )
    status_code = 200 if payload.status == "ok" else 503
    return JSONResponse(payload.model_dump(), status_code=status_code)
