#!/usr/bin/env bash
# Prove the engine the stack is running is the image the stack file pins, and that it
# carries its model weights (FR-022, research.md R4).
#
#   ./ops/verify-engine-image.sh [stack-name]        default stack name: pdf2md
#
# This is the check `ops/save-images.sh` used to perform at export time, before that
# script was retired with the air-gapped delivery path. It matters because the failure it
# catches is quiet: an engine image without weights deploys cleanly, reports healthy, and
# then fails on the first scanned page.
set -uo pipefail

STACK="${1:-pdf2md}"
COMPOSE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/deploy/docker-compose.yml"
ARTIFACTS_PATH="${ARTIFACTS_PATH:-/opt/app-root/src/.cache/docling/models}"
failures=0

DOCLING="$(docker ps -q \
  --filter "label=com.docker.compose.project=${STACK}" \
  --filter "label=com.docker.compose.service=docling" | head -n1)"

if [ -z "$DOCLING" ]; then
  echo "could not find the docling service of stack '${STACK}' — is it running?" >&2
  echo "usage: $0 [stack-name]" >&2
  exit 2
fi

echo "== the running engine must be the image the stack file pins =="
pinned="$(grep -o 'ghcr.io/docling-project/docling-serve-cpu[^}"]*' "$COMPOSE" | head -n1)"
pinned_digest="${pinned##*@}"
running_digest="$(docker inspect -f '{{index .RepoDigests 0}}' \
  "$(docker inspect -f '{{.Image}}' "$DOCLING")" 2>/dev/null)"
running_digest="${running_digest##*@}"

if [ -z "$pinned_digest" ] || [ "$pinned_digest" = "$pinned" ]; then
  echo "  FAIL  the stack file pins no digest for the engine — a tag alone is not a pin"
  failures=$((failures + 1))
elif [ "$running_digest" = "$pinned_digest" ]; then
  echo "  ok    running ${running_digest}"
else
  echo "  FAIL  running ${running_digest:-unknown}"
  echo "        pinned  ${pinned_digest}"
  echo "        The deployed engine is not the one this repository describes."
  failures=$((failures + 1))
fi

echo
echo "== the models must be inside the image, not fetched at runtime =="
listing="$(docker exec "$DOCLING" sh -c "ls -A '${ARTIFACTS_PATH}' 2>/dev/null" || true)"
if [ -n "$listing" ]; then
  count="$(echo "$listing" | wc -l | tr -d ' ')"
  echo "  ok    ${ARTIFACTS_PATH} holds ${count} entries:"
  echo "$listing" | sed 's/^/          /'
else
  echo "  FAIL  ${ARTIFACTS_PATH} is empty or missing"
  echo "        This engine would try to download models at first use, which cannot"
  echo "        succeed: the container has no route out. A '-slim' variant looks exactly"
  echo "        like this. Pin an image that ships its weights and redeploy."
  failures=$((failures + 1))
fi

echo
echo "== the configured recognition language must be servable from those models =="
# The stack asks for a language (FR-039) and the container cannot download anything, so
# the weights that serve it have to be here already. EasyOCR's `latin_g2` is the model
# every Latin-script language resolves to, and `craft` is the detector it needs. This is
# the German equivalent of the slim-image check above: without them, scanned pages come
# back in the wrong alphabet rather than failing.
preset="$(grep -o 'PDF2MD_OCR_PRESET:.*' "$COMPOSE" | head -n1 | sed 's/.*:-//; s/}.*//')"
if [ "${preset:-auto}" = "easyocr" ]; then
  easyocr="$(docker exec "$DOCLING" sh -c "ls -A '${ARTIFACTS_PATH}/EasyOcr' 2>/dev/null" || true)"
  missing=""
  echo "$easyocr" | grep -qi 'latin_g2' || missing="latin_g2"
  echo "$easyocr" | grep -qi 'craft'    || missing="${missing:+${missing}, }craft"
  if [ -z "$missing" ]; then
    echo "  ok    EasyOcr carries craft and latin_g2 — Latin-script languages are servable"
  else
    echo "  FAIL  EasyOcr is missing: ${missing}"
    echo "        PDF2MD_OCR_PRESET=easyocr would fail on every scanned page, because"
    echo "        the weights cannot be fetched from inside this container."
    failures=$((failures + 1))
  fi
else
  echo "  note  PDF2MD_OCR_PRESET is '${preset:-auto}': the engine chooses, and its own"
  echo "        default reads English and Chinese. German scans lose their umlauts."
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "PASS — the deployed engine matches the pin and carries its own models."
  exit 0
fi
echo "FAIL — ${failures} check(s) failed. Do not treat this deployment as offline-capable."
exit 1
