"""A stub `docling-serve` implementing the contract in contracts/docling-serve.md.

Enough of the async task API to drive the dispatcher, plus the two behaviours that
actually bite in production: single-use results, and failures at each step. Tests
mount it over an httpx ASGI transport, so no 4.4 GB image is needed to develop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

DEFAULT_MARKDOWN = (
    "# Converted document\n\n"
    "Body text recovered from the PDF, in reading order across both columns of the page. "
    "The section below carries a table so callers exercising the default behaviour get a "
    "document of plausible size rather than one that trips the suspect-yield floor.\n\n"
    "| Quarter | Revenue |\n| --- | --- |\n| Q1 | 1,204 |\n| Q2 | 1,588 |\n"
)


@dataclass
class TaskBehavior:
    """How the stub should treat one submission, matched by uploaded filename."""

    markdown: str = DEFAULT_MARKDOWN
    result_status: str = "success"  # success | partial_success | skipped | failure
    errors: list[str] = field(default_factory=list)
    page_count: int | None = None
    processing_time: float = 1.5
    polls_pending: int = 1
    """Polls answered `pending` before the task starts."""

    polls_running: int = 1
    """Polls answered `started` before the task finishes."""

    never_finishes: bool = False
    """Stays `started` forever, for the timeout watchdog."""

    fail_submission: bool = False
    task_status_on_finish: str = "success"  # success | failure
    result_http_error: int | None = None
    """Return this HTTP status from /v1/result, to exercise a lost result."""


@dataclass
class _Task:
    task_id: str
    filename: str
    behavior: TaskBehavior
    polls: int = 0
    result_consumed: bool = False
    position: int = 0


class StubEngine:
    """State plus a FastAPI app. One instance per test."""

    def __init__(self, api_key: str = "test-key") -> None:
        self.api_key = api_key
        self.default_behavior = TaskBehavior()
        self.behaviors: dict[str, TaskBehavior] = {}
        self.tasks: dict[str, _Task] = {}
        self.submissions: list[dict[str, Any]] = []
        self.result_fetches: list[str] = []
        self.healthy = True
        self.models_ready = True
        self.reachable = True
        self.require_api_key = True
        self.app = self._build_app()

    # --- test controls ----------------------------------------------------

    def set_behavior(self, filename: str, behavior: TaskBehavior) -> None:
        self.behaviors[filename] = behavior

    def behavior_for(self, filename: str) -> TaskBehavior:
        return self.behaviors.get(filename, self.default_behavior)

    def reset_tasks(self) -> None:
        """Forget every task, as an engine restart would."""
        self.tasks.clear()

    # --- app --------------------------------------------------------------

    def _check_key(self, request: Request) -> None:
        if not self.reachable:
            raise HTTPException(status_code=503, detail="engine unreachable")
        if self.require_api_key and request.headers.get("X-Api-Key") != self.api_key:
            raise HTTPException(status_code=401, detail="bad api key")

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        async def health() -> JSONResponse:
            if not self.reachable:
                raise HTTPException(status_code=503, detail="engine unreachable")
            if not self.healthy:
                return JSONResponse({"status": "unhealthy"}, status_code=503)
            return JSONResponse({"status": "ok"})

        @app.get("/ready")
        async def ready() -> JSONResponse:
            # Upstream gates this on model loading, which is why it — and not /health —
            # is what `depends_on: service_healthy` and our own health check use.
            if not self.reachable:
                raise HTTPException(status_code=503, detail="engine unreachable")
            if not self.models_ready or not self.healthy:
                raise HTTPException(status_code=503, detail="Models not yet loaded")
            return JSONResponse({"status": "ok"})

        @app.post("/v1/convert/file/async")
        async def submit(
            request: Request,
            files: UploadFile,
            from_formats: list[str] = Form(default=[]),
            to_formats: list[str] = Form(default=[]),
            do_ocr: str = Form(default="false"),
        ) -> JSONResponse:
            self._check_key(request)
            payload = await files.read()
            self.submissions.append(
                {
                    "filename": files.filename,
                    "size": len(payload),
                    "from_formats": from_formats,
                    "to_formats": to_formats,
                    "do_ocr": do_ocr,
                }
            )
            behavior = self.behavior_for(files.filename or "")
            if behavior.fail_submission:
                raise HTTPException(status_code=500, detail="engine refused the submission")

            task = _Task(
                task_id=str(uuid.uuid4()),
                filename=files.filename or "",
                behavior=behavior,
                position=len([t for t in self.tasks.values() if not t.result_consumed]),
            )
            self.tasks[task.task_id] = task
            return JSONResponse(
                {
                    "task_id": task.task_id,
                    "task_status": "pending",
                    "task_position": task.position,
                    "task_meta": None,
                }
            )

        @app.get("/v1/status/poll/{task_id}")
        async def poll(request: Request, task_id: str) -> JSONResponse:
            self._check_key(request)
            task = self.tasks.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="unknown task")
            task.polls += 1
            behavior = task.behavior
            if task.polls <= behavior.polls_pending:
                status = "pending"
            elif behavior.never_finishes or task.polls <= (
                behavior.polls_pending + behavior.polls_running
            ):
                status = "started"
            else:
                status = behavior.task_status_on_finish
            return JSONResponse(
                {
                    "task_id": task_id,
                    "task_status": status,
                    "task_position": task.position if status == "pending" else None,
                    "task_meta": None,
                }
            )

        @app.get("/v1/result/{task_id}")
        async def result(request: Request, task_id: str) -> JSONResponse:
            self._check_key(request)
            task = self.tasks.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="unknown task")
            self.result_fetches.append(task_id)
            behavior = task.behavior
            if behavior.result_http_error:
                raise HTTPException(status_code=behavior.result_http_error, detail="result error")
            if task.result_consumed:
                # DOCLING_SERVE_SINGLE_USE_RESULTS: a result is served exactly once.
                raise HTTPException(status_code=404, detail="result already consumed")
            task.result_consumed = True
            document: dict[str, Any] = {
                "md_content": behavior.markdown,
                "json_content": {},
                "html_content": "",
                "text_content": "",
                "doctags_content": "",
            }
            if behavior.page_count is not None:
                document["page_count"] = behavior.page_count
            return JSONResponse(
                {
                    "document": document,
                    "status": behavior.result_status,
                    "processing_time": behavior.processing_time,
                    "timings": {},
                    "errors": behavior.errors,
                }
            )

        return app
