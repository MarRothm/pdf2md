"""The page must work for a client with no internet at all (FR-025)."""

import re

import pytest

from pdf2md.main import STATIC_DIR

pytestmark = pytest.mark.unit

ASSETS = ["index.html", "app.js", "styles.css"]

# Any absolute or protocol-relative URL in a page asset is a request that leaves the
# Mac mini for a client whose browser can make it.
EXTERNAL_URL = re.compile(r"""(?:https?:)?//(?!127\.0\.0\.1|localhost)[a-z0-9.-]+""", re.I)
COMMENT_LINE = re.compile(r"^\s*(//|\*|/\*|<!--)")


def _asset_text(name: str) -> str:
    return (STATIC_DIR / name).read_text()


@pytest.mark.parametrize("name", ASSETS)
def test_asset_exists(name):
    assert (STATIC_DIR / name).is_file()


@pytest.mark.parametrize("name", ASSETS)
def test_no_asset_references_an_external_origin(name):
    offenders = [
        line.strip()
        for line in _asset_text(name).splitlines()
        if EXTERNAL_URL.search(line) and not COMMENT_LINE.match(line)
    ]
    assert offenders == []


@pytest.mark.parametrize("name", ASSETS)
def test_no_cdn_font_or_analytics_hosts(name):
    code = "\n".join(
        line.lower() for line in _asset_text(name).splitlines() if not COMMENT_LINE.match(line)
    )
    for host in (
        "cdn",
        "googleapis",
        "gstatic",
        "unpkg",
        "jsdelivr",
        "cloudflare",
        "fonts.",
        "analytics",
        "googletagmanager",
    ):
        assert host not in code, f"{name} mentions {host}"


def test_the_page_declares_no_remote_stylesheet_or_script():
    html = _asset_text("index.html")
    for attribute in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert attribute.startswith(("/static/", "data:")), attribute


def test_no_web_font_is_requested():
    css = _asset_text("styles.css")
    assert "@font-face" not in css
    assert "@import" not in css
