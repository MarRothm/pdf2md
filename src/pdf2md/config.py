"""Settings for the web service.

Every variable is `PDF2MD_`-prefixed and matches the table in
`specs/001-docling-pdf2md-stack/contracts/stack.md`, including its default.
`PDF2MD_ENGINE_API_KEY` has no default: the service refuses to start without it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PDF2MD_", extra="ignore")

    # --- engine -----------------------------------------------------------
    engine_url: str = "http://docling:5001"
    engine_api_key: str
    engine_health_path: str = "/ready"
    """`/ready` gates on model loading; `/health` only says the process is up."""

    engine_workers: int = 2
    """Mirrors DOCLING_SERVE_ENG_LOC_NUM_WORKERS; bounds our in-flight submissions."""

    in_flight_buffer: int = 1
    """Extra submissions allowed beyond the engine's worker count (FR-027)."""

    # --- storage ----------------------------------------------------------
    db_path: Path = Path("/data/db/pdf2md.sqlite")
    inbox_path: Path = Path("/data/inbox")
    outbox_path: Path = Path("/data/outbox")

    # --- limits and timing ------------------------------------------------
    max_upload_bytes: int = 209_715_200
    min_free_bytes: int = 67_108_864
    """Refuse uploads (507) below this much free space, naming the location that is full."""

    job_timeout_seconds: int = 2700
    poll_interval_seconds: float = 2.0
    inbox_retention_hours: int = 48
    failed_inbox_retention_days: int = 14
    suspect_min_chars_per_page: int = 50
    suspect_min_chars_floor: int = 200
    """Flat floor applied when the engine reports no page count (data-model.md, FR-029)."""

    job_history_days: int = 30

    # --- splitting (FR-033 through FR-037) --------------------------------
    part_max_pages: int = 100
    """Documents longer than this are converted in parts (FR-034).

    Sized to the engine's *time* ceiling, not its page ceiling: at 10s/page a part takes
    ~1000s against DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT of 2400s. A value chosen to sit just
    under the 2000-page limit would time out instead (research.md R12).
    """

    max_total_pages: int = 10_000
    """Refused at upload above this, for its length — never as damaged (FR-036)."""

    parts_in_flight: int = 2
    """Parts of one document in the engine at once, so a long document cannot starve
    short ones queued behind it (research.md R14)."""

    section_split_threshold_bytes: int = 1_048_576
    """Above this much joined Markdown, output is written as section files (FR-033)."""

    section_min_bytes: int = 16_384
    """Sections below this merge into their predecessor rather than standing alone."""

    section_max_bytes: int = 524_288
    """Sections above this are divided at the next heading level down."""

    # --- operational ------------------------------------------------------
    log_level: str = "INFO"
    dispatcher_enabled: bool = True
    """Off in tests that drive the dispatcher by hand."""

    require_private_engine_url: bool = True
    """Startup refuses a publicly routable engine address unless disabled (FR-021)."""

    engine_connect_timeout: float = 10.0
    engine_read_timeout: float = 120.0

    @property
    def max_in_flight(self) -> int:
        return max(1, self.engine_workers + self.in_flight_buffer)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("engine_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
