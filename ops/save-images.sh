#!/usr/bin/env bash
# Build and export both images on a CONNECTED machine, for transfer to the air-gapped
# Mac mini. Run from the repository root: ./ops/save-images.sh [output-directory]
#
# The one check that matters here is step 2: an engine image without baked-in model
# weights will try to download them on first use and fail on a host with no egress.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/dist}"
PLATFORM="${PLATFORM:-linux/arm64}"
ARTIFACTS_PATH="${ARTIFACTS_PATH:-/opt/app-root/src/.cache/docling/models}"

# Defaults match deploy/.env.example; override by exporting before running.
ENGINE_IMAGE="${ENGINE_IMAGE:-ghcr.io/docling-project/docling-serve-cpu:v1.18.0}"
WEB_IMAGE="${WEB_IMAGE:-pdf2md-web:1.0.0}"

case "$ENGINE_IMAGE" in
  *:latest) echo "refusing to ship a 'latest' tag — pin an exact version" >&2; exit 1 ;;
  *slim*)   echo "refusing a '-slim' engine variant — it ships without model weights" >&2; exit 1 ;;
esac

mkdir -p "$OUT_DIR"
echo "==> 1/5 pulling ${ENGINE_IMAGE} for ${PLATFORM}"
docker pull --platform "$PLATFORM" "$ENGINE_IMAGE"

echo "==> 2/5 verifying the models are baked into the image"
model_listing="$(docker run --rm --platform "$PLATFORM" --entrypoint sh "$ENGINE_IMAGE" \
  -c "ls -A '${ARTIFACTS_PATH}' 2>/dev/null" || true)"
if [ -z "$model_listing" ]; then
  cat >&2 <<MSG
ABORTING: ${ARTIFACTS_PATH} is empty or missing in ${ENGINE_IMAGE}.

This image would try to download models at first use, which fails on an air-gapped
host. Pick an engine image that ships its weights (not a -slim variant) and re-run.
MSG
  exit 1
fi
echo "    models present:"
echo "$model_listing" | sed 's/^/      /'

echo "==> 3/5 building ${WEB_IMAGE} for ${PLATFORM}"
docker build --platform "$PLATFORM" -t "$WEB_IMAGE" "$REPO_ROOT"

echo "==> 4/5 saving archives to ${OUT_DIR}"
engine_archive="${OUT_DIR}/docling-serve-cpu.tar.gz"
web_archive="${OUT_DIR}/pdf2md-web.tar.gz"
docker save "$ENGINE_IMAGE" | gzip -1 > "$engine_archive"
docker save "$WEB_IMAGE" | gzip -1 > "$web_archive"

echo "==> 5/5 writing checksums"
(
  cd "$OUT_DIR"
  shasum -a 256 "$(basename "$engine_archive")" "$(basename "$web_archive")" > SHA256SUMS
  cat SHA256SUMS
)

cat > "${OUT_DIR}/IMAGES" <<MSG
ENGINE_IMAGE=${ENGINE_IMAGE}
WEB_IMAGE=${WEB_IMAGE}
PLATFORM=${PLATFORM}
MSG

echo
echo "Done. Move these to the Mac mini, then run ops/load-images.sh <directory>:"
ls -lh "$OUT_DIR"
