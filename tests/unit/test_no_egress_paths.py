"""The only address the service ever calls is the configured engine (FR-021)."""

import ast
from pathlib import Path

import pytest

import pdf2md

pytestmark = pytest.mark.unit

SOURCE_DIR = Path(pdf2md.__file__).parent
SOURCE_FILES = sorted(SOURCE_DIR.rglob("*.py"))

FORBIDDEN_MODULES = {"urllib.request", "urllib3", "requests", "aiohttp", "ftplib", "smtplib"}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(), filename=str(path))


def test_no_http_library_other_than_httpx_is_imported():
    for path in SOURCE_FILES:
        for node in ast.walk(_tree(path)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name not in FORBIDDEN_MODULES, f"{path.name} imports {name}"


def test_httpx_clients_are_constructed_only_in_the_engine_client():
    """One outbound client, and its base URL comes from settings."""
    constructing = []
    for path in SOURCE_FILES:
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name in {"AsyncClient", "Client"}:
                constructing.append(path.name)
    assert set(constructing) <= {"docling_client.py"}, constructing


def test_the_engine_client_takes_its_base_url_from_configuration():
    source = (SOURCE_DIR / "docling_client.py").read_text()
    assert "base_url=self.base_url" in source
    for scheme_use in ("http://", "https://"):
        for line in source.splitlines():
            if scheme_use in line:
                assert line.lstrip().startswith("#"), line


def test_the_app_passes_the_configured_engine_url_to_the_client():
    source = (SOURCE_DIR / "main.py").read_text()
    assert "base_url=resolved.engine_url" in source


def test_a_public_engine_address_is_refused_at_startup():
    from pdf2md.main import PublicEngineAddressError, assert_engine_url_is_private

    assert_engine_url_is_private("http://127.0.0.1:5001")
    assert_engine_url_is_private("http://10.0.0.19:5001")
    assert_engine_url_is_private("http://192.168.1.10:5001")
    assert_engine_url_is_private("http://docling:5001")  # unresolvable here: allowed

    with pytest.raises(PublicEngineAddressError):
        assert_engine_url_is_private("http://1.1.1.1:5001")
    with pytest.raises(PublicEngineAddressError):
        assert_engine_url_is_private("http://93.184.216.34/")
