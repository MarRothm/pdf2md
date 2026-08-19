"""HTTP layer: shared error type and request-scoped accessors."""

from __future__ import annotations

from fastapi import Request

from pdf2md.config import Settings
from pdf2md.db import Database
from pdf2md.docling_client import DoclingClient
from pdf2md.storage import Storage


class ApiError(Exception):
    """An error the page can show verbatim (contracts/web-api.md error shape)."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def payload(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def db_of(request: Request) -> Database:
    return request.app.state.db


def storage_of(request: Request) -> Storage:
    return request.app.state.storage


def engine_of(request: Request) -> DoclingClient:
    return request.app.state.engine
