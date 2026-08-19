"""Logging setup.

Every line about a job carries the job id, the source filename, and the outcome,
so a failure is diagnosable from the Portainer log view alone (FR-019).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def log_job(
    logger: logging.Logger,
    event: str,
    *,
    job_id: str,
    filename: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one job-scoped log line as `event job_id=… file=… key=value …`."""
    parts = [f"job_id={job_id}", f'file="{filename}"']
    parts += [
        f'{key}="{value}"' if isinstance(value, str) else f"{key}={value}"
        for key, value in fields.items()
        if value is not None
    ]
    logger.log(level, "%s %s", event, " ".join(parts))
