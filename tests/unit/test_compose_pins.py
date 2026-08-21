"""The stack file's image references are a deployment invariant, not a formatting choice.

A tag can be re-pointed upstream; a digest cannot. `pull_policy: never` belonged to the
air-gapped delivery path and would now break a deploy on a host that has not hand-loaded
the image. These assertions are cheap and they fail in CI rather than on the Mac mini.

See contracts/stack.md "Deployment invariants" and research.md R5.
"""

import re
from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"

# Images whose digest cannot exist yet because they have never been published. Empty
# since v1.0.0 was published — an entry here is a temporary state, not an exemption.
AWAITING_FIRST_PUBLISH: set[str] = set()

pytestmark = pytest.mark.unit


def _image_defaults() -> dict[str, str]:
    """Every service's image reference, with `${VAR:-default}` resolved to its default.

    Portainer supplies the variables, so the default in the file is what a deployment
    gets when nobody overrides it — which makes the default the thing worth asserting on.
    """
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    resolved = {}
    for name, service in compose["services"].items():
        image = service["image"]
        if image.startswith("${") and ":-" in image:
            image = image.split(":-", 1)[1].rstrip("}")
        resolved[name] = image
    return resolved


def test_no_service_pulls_from_the_host_only() -> None:
    """`pull_policy: never` would fail any deploy on a host that has not hand-loaded."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    for name, service in compose["services"].items():
        assert service.get("pull_policy") != "never", (
            f"{name} sets pull_policy: never, which belongs to the retired air-gap "
            f"delivery path (research.md R5)"
        )


def test_no_moving_tags() -> None:
    for name, image in _image_defaults().items():
        assert not image.endswith(":latest"), f"{name} uses a moving `latest` tag (FR-032)"


def test_engine_is_not_a_slim_variant() -> None:
    """A `-slim` engine ships without model weights: it deploys, reports healthy, and
    then fails on the first scanned page (research.md R4)."""
    assert "slim" not in _image_defaults()["docling"]


def test_images_are_pinned_by_digest() -> None:
    for name, image in _image_defaults().items():
        repository = image.split("@", 1)[0].rsplit(":", 1)[0]
        if repository in AWAITING_FIRST_PUBLISH:
            continue
        assert "@sha256:" in image, (
            f"{name} is pinned by tag alone; a tag can be re-pointed upstream, so the "
            f"digest is the actual pin (FR-032)"
        )


def test_the_env_example_carries_no_second_copy_of_a_digest() -> None:
    """The stack file is the only place a digest is written down.

    `.env.example` used to carry its own copy of both image references, so a release had
    to transcribe the same 71-character digest twice with nothing checking that the two
    agreed. The variables remain documented there as deliberate overrides, commented out.
    """
    env_example = COMPOSE_PATH.parent / ".env.example"
    offenders = [
        line.strip()
        for line in env_example.read_text().splitlines()
        if "sha256:" in line and not line.lstrip().startswith("#")
    ]
    assert offenders == [], (
        "an image digest in .env.example is a copy that nothing keeps current, and it "
        "overrides the pin the repository maintains"
    )


def test_every_documented_setting_can_actually_be_set() -> None:
    """A variable named in the operator's guide but absent from the stack file is a lie.

    Portainer passes stack variables to the container only where the compose file spells
    them out, so `PDF2MD_PART_MAX_PAGES` documented as tunable and missing from
    `environment:` reads as a knob and behaves as a constant — which is how a part size
    too large for the engine's time ceiling stayed too large.
    """
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    wired = set(re.findall(r"PDF2MD_[A-Z_]+", yaml.safe_dump(compose["services"]["web"])))
    documented = set(re.findall(r"PDF2MD_[A-Z_]+", (COMPOSE_PATH.parent / "README.md").read_text()))
    missing = sorted(documented - wired)
    assert not missing, f"documented but not passed to the service: {', '.join(missing)}"


def test_a_recognition_language_is_paired_with_an_explicit_engine() -> None:
    """`ocr_lang` under the `auto` preset is a request nobody honours.

    `auto` selects RapidOCR on this image, whose bundled weights are English and Chinese;
    a language it has no model for cannot be fetched at runtime (FR-022). Only an engine
    named explicitly — `easyocr`, whose `latin_g2` weights are baked in — can satisfy one.
    """
    web = yaml.safe_load(COMPOSE_PATH.read_text())["services"]["web"]["environment"]
    language = str(web.get("PDF2MD_OCR_LANG", ""))
    preset = str(web.get("PDF2MD_OCR_PRESET", ""))
    if ":-" in language and language.split(":-", 1)[1].rstrip("}"):
        assert ":-" in preset and preset.split(":-", 1)[1].rstrip("}") not in ("", "auto"), (
            "PDF2MD_OCR_LANG is set while the OCR engine is left to `auto`"
        )


def test_the_page_does_not_wait_for_the_engine_to_be_healthy() -> None:
    """An engine that never becomes healthy must not take the page down with it.

    The page is what reports the engine's state; gating it on that state means the one
    failure you most need to see is the one that hides the interface (FR-041). Uploads
    while the engine is away are already handled — they queue.
    """
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    condition = compose["services"]["web"]["depends_on"]["docling"]["condition"]
    assert condition == "service_started", (
        "web waits for the engine to be healthy; a sick engine then hides the page"
    )


def test_every_shipped_setting_is_in_the_stack_contract() -> None:
    """`contracts/stack.md` is where a setting's default and its reasoning live.

    A setting that ships without a row there is a value nobody can question later — which
    is how `PART_MAX_PAGES=100` kept its "at 10 s/page" rationale through three days of
    evidence that the real figure was under one.
    """
    web = yaml.safe_dump(yaml.safe_load(COMPOSE_PATH.read_text())["services"]["web"])
    contract = (
        COMPOSE_PATH.parents[1] / "specs" / "001-docling-pdf2md-stack" / "contracts" / "stack.md"
    ).read_text()
    missing = sorted(
        name for name in set(re.findall(r"PDF2MD_[A-Z_]+", web)) if name not in contract
    )
    assert not missing, f"shipped but undocumented in stack.md: {', '.join(missing)}"
