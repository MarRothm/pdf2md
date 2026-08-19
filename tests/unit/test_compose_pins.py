"""The stack file's image references are a deployment invariant, not a formatting choice.

A tag can be re-pointed upstream; a digest cannot. `pull_policy: never` belonged to the
air-gapped delivery path and would now break a deploy on a host that has not hand-loaded
the image. These assertions are cheap and they fail in CI rather than on the Mac mini.

See contracts/stack.md "Deployment invariants" and research.md R5.
"""

from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"

# Images whose digest cannot exist yet because they have never been published.
# Remove an entry as soon as its first publish workflow has run (tasks T100, T102).
AWAITING_FIRST_PUBLISH = {"ghcr.io/marrothm/pdf2md-web"}

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
