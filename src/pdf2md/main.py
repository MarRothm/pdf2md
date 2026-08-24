"""FastAPI application: the page, the JSON API, and the dispatcher's lifetime.

The app owns one SQLite registry, one outbox, and one engine client. Nothing here
reaches the internet: the only outbound address is `PDF2MD_ENGINE_URL`, and startup
refuses a publicly routable one (FR-021).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from pdf2md.api import ApiError
from pdf2md.api.health import router as health_router
from pdf2md.api.jobs import router as jobs_router
from pdf2md.api.uploads import router as uploads_router
from pdf2md.config import Settings, get_settings
from pdf2md.db import Database
from pdf2md.dispatcher import Dispatcher
from pdf2md.docling_client import DoclingClient
from pdf2md.logging_config import configure_logging
from pdf2md.storage import OutOfSpaceError, Storage

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
_page = (STATIC_DIR / "index.html").read_text()


class PublicEngineAddressError(RuntimeError):
    """The configured engine address is on the public internet — refuse to run."""


def assert_engine_url_is_private(engine_url: str) -> None:
    """Refuse to start against a public engine host.

    The engine belongs on the stack's internal network. A public address here would
    mean documents leaving the Mac mini, which no amount of network topology could
    undo. A name that does not resolve yet is allowed — inside the stack it resolves
    to a container address, and `depends_on` already sequences startup.
    """
    host = urlsplit(engine_url).hostname
    if not host:
        raise PublicEngineAddressError(f"{engine_url!r} has no host")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        logger.warning('engine_host_unresolved host="%s" — continuing', host)
        return
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if parsed.is_global:
            raise PublicEngineAddressError(
                f"engine host {host!r} resolves to the public address {address}; "
                "the engine must sit on the stack's internal network"
            )


def create_app(
    settings: Settings | None = None,
    *,
    engine_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved.require_private_engine_url:
            assert_engine_url_is_private(resolved.engine_url)

        storage = Storage(resolved.inbox_path, resolved.outbox_path)
        storage.ensure_directories()
        db = Database(resolved.db_path)
        db.migrate()
        engine = DoclingClient(
            base_url=resolved.engine_url,
            api_key=resolved.engine_api_key,
            transport=engine_transport,
            connect_timeout=resolved.engine_connect_timeout,
            read_timeout=resolved.engine_read_timeout,
            health_path=resolved.engine_health_path,
            ocr_preset=resolved.ocr_preset,
            ocr_languages=resolved.ocr_languages,
            extract_images=resolved.extract_images,
        )
        dispatcher = Dispatcher(db=db, storage=storage, engine=engine, settings=resolved)

        app.state.settings = resolved
        app.state.storage = storage
        app.state.db = db
        app.state.engine = engine
        app.state.dispatcher = dispatcher

        if resolved.dispatcher_enabled:
            await dispatcher.start()
        else:
            dispatcher.recover_in_flight()
        logger.info(
            'started version=%s engine="%s" outbox="%s"',
            app.version,
            resolved.engine_url,
            resolved.outbox_path,
        )
        try:
            yield
        finally:
            await dispatcher.stop()
            await engine.aclose()

    app = FastAPI(
        title="pdf2md",
        version=_version(),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.include_router(uploads_router)
    app.include_router(jobs_router)
    app.include_router(health_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        """Container healthcheck: cheap, no engine call, no database write."""
        if not request.app.state.db.readable():
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        """The page, with its assets addressed by version.

        `StaticFiles` sends no `Cache-Control`, so a browser is free to reuse `app.js`
        from cache without revalidating — and did: after a release the page kept running
        the previous version's script, so a feature that had shipped appeared to be
        missing. The query string changes with every release, and this document is served
        `no-cache` so the new one is actually seen.
        """
        return HTMLResponse(
            _page.replace("__VERSION__", _version()),
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    _install_error_handlers(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    """One error shape for the page, whatever went wrong (contracts/web-api.md)."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(error.payload(), status_code=error.status_code)

    @app.exception_handler(OutOfSpaceError)
    async def _out_of_space(request: Request, error: OutOfSpaceError) -> JSONResponse:
        message = (
            f"There is no space left in the {error.location} folder on the server. "
            "Ask the operator to free some space, then try again."
        )
        logger.error("out_of_space location=%s", error.location)
        return JSONResponse({"error": {"code": "no_space", "message": message}}, status_code=507)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "The request was not understood. "
                    "Choose one or more PDF files and try again.",
                }
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": _code_for(error.status_code), "message": str(error.detail)}},
            status_code=error.status_code,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled_error path=%s", request.url.path)
        return JSONResponse(
            {
                "error": {
                    "code": "server_error",
                    "message": "Something went wrong on the server. "
                    "Try again; if it keeps happening, ask the operator to check the logs.",
                }
            },
            status_code=500,
        )


def _code_for(status_code: int) -> str:
    return {404: "not_found", 405: "method_not_allowed", 413: "request_too_large"}.get(
        status_code, "error"
    )


def _version() -> str:
    from pdf2md import __version__

    return __version__


def __getattr__(name: str) -> Any:
    """`uvicorn pdf2md.main:app` builds the app from the environment on first access."""
    if name == "app":
        return create_app()
    raise AttributeError(name)
