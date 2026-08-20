"""Settings defaults, checked against contracts/stack.md."""

import pytest
from pydantic import ValidationError

from pdf2md.config import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("PDF2MD_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_engine_api_key_is_required(clean_env):
    with pytest.raises(ValidationError):
        Settings()


def test_defaults_match_the_stack_contract(clean_env):
    clean_env.setenv("PDF2MD_ENGINE_API_KEY", "k")
    settings = Settings()
    assert settings.engine_url == "http://docling:5001"
    assert str(settings.db_path) == "/data/db/pdf2md.sqlite"
    assert str(settings.inbox_path) == "/data/inbox"
    assert str(settings.outbox_path) == "/data/outbox"
    assert settings.max_upload_bytes == 209_715_200
    assert settings.job_timeout_seconds == 2700
    assert settings.poll_interval_seconds == 2
    assert settings.inbox_retention_hours == 48
    assert settings.failed_inbox_retention_days == 14
    assert settings.suspect_min_chars_per_page == 50
    assert settings.job_history_days == 30
    assert settings.log_level == "INFO"


def test_environment_overrides_are_read_with_the_prefix(clean_env):
    clean_env.setenv("PDF2MD_ENGINE_API_KEY", "k")
    clean_env.setenv("PDF2MD_MAX_UPLOAD_BYTES", "1024")
    clean_env.setenv("PDF2MD_LOG_LEVEL", "debug")
    clean_env.setenv("PDF2MD_ENGINE_URL", "http://engine:5001/")
    settings = Settings()
    assert settings.max_upload_bytes == 1024
    assert settings.log_level == "DEBUG"
    assert settings.engine_url == "http://engine:5001"


def test_in_flight_ceiling_follows_the_engine_worker_count(clean_env):
    clean_env.setenv("PDF2MD_ENGINE_API_KEY", "k")
    clean_env.setenv("PDF2MD_ENGINE_WORKERS", "2")
    clean_env.setenv("PDF2MD_IN_FLIGHT_BUFFER", "1")
    assert Settings().max_in_flight == 3


def test_the_version_has_exactly_one_source():
    """`/api/health` and the startup log report `__version__`; the build must use the same.

    It drifted silently for two releases — the version was a literal in `__init__.py` and
    another in `pyproject.toml`, and only the second was ever bumped, so the 1.2.0 image
    reported itself as 1.0.0. `pyproject.toml` now derives from the module.
    """
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text())

    assert "version" not in config["project"], "a second literal would drift again"
    assert config["project"]["dynamic"] == ["version"]
    assert config["tool"]["hatch"]["version"]["path"] == "src/pdf2md/__init__.py"
